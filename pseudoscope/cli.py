"""
Command-line interface for PseudoScope.

Step 1: parse and validate CLI input.
Step 2: read the target source file into memory.
Step 3: locate the target function body range.
Step 4: generate default-return mutations in memory.
Step 6: run a baseline test command.
Step 7: run mutation tests (write → test → restore per mutation).
Step 8: classify results and write JSON to ``config.output_path``.

File sweep (omit ``--function``): discover functions with Tree-sitter, baseline
once, then analyze each function. Final JSON is written only when the sweep
completes; a hidden partial file is updated after each function.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Sequence

from pseudoscope.locate import (
    FunctionBodyLocation,
    FunctionLocateError,
    locate_function_body,
)
from pseudoscope.mutate import (
    MutationError,
    MutatedSource,
    generate_default_return_mutations,
    replacement_return_line,
)
from pseudoscope.executor import (
    MutationExecutionError,
    MutationRunResult,
    run_mutation_tests,
)
from pseudoscope.models import ConfigError, PseudoScopeConfig
from pseudoscope.discover import DiscoverError, discover_functions
from pseudoscope.results import (
    ResultWriteError,
    build_function_analysis_result,
    display_status,
    format_result_table,
    write_json_result,
)
from pseudoscope.sweep import SweepAbortError, run_file_sweep
from pseudoscope.runner import TestRunError, TestRunResult, run_test_command
from pseudoscope.source import SourceFile, SourceReadError, read_source_file
from pseudoscope.validation import build_config


def build_parser() -> argparse.ArgumentParser:
    """Define PseudoScope CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="pseudoscope",
        description=(
            "PseudoScope detects pseudo-tested code in C/C++ projects. "
            "Current release: validate input, read source, locate function, "
            "generate mutations, run tests, and write JSON results. "
            "Omit --function to analyze every function in the file (file sweep)."
        ),
    )
    parser.add_argument(
        "--project-root",
        required=True,
        metavar="PATH",
        help="Root directory of the target project.",
    )
    parser.add_argument(
        "--file",
        required=True,
        metavar="REL_PATH",
        help="Source file path relative to --project-root (e.g. src/calculator.cpp).",
    )
    parser.add_argument(
        "--function",
        default=None,
        metavar="NAME",
        help=(
            "Function or method to analyze. If omitted, all functions in "
            "--file are analyzed (file sweep; .c / .cpp only)."
        ),
    )
    parser.add_argument(
        "--test-command",
        required=True,
        metavar="CMD",
        help="Shell command to run tests from the project root.",
    )
    parser.add_argument(
        "--output",
        default="pseudoscope-results.json",
        metavar="PATH",
        help=(
            "JSON output path (default: pseudoscope-results.json)."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        metavar="SECONDS",
        help="Planned test timeout in seconds (default: 60).",
    )
    return parser


def print_config_summary(config: PseudoScopeConfig) -> None:
    """Print a human-readable summary of the validated configuration."""
    print("PseudoScope configuration loaded successfully.")
    print()
    print(f"Project root: {config.project_root}")
    print(f"Target file: {config.target_file}")
    if config.function_name:
        print(f"Function: {config.function_name}")
    else:
        print("Mode: file sweep (all functions in target file)")
    print(f"Test command: {config.test_command}")
    print(f"Output file: {config.output_path}")
    print(f"Timeout: {config.timeout_seconds} seconds")


def print_source_summary(source: SourceFile) -> None:
    """Print a short summary after the source file is loaded (no file contents)."""
    print()
    print("Source file loaded successfully.")
    print(f"Source lines: {source.line_count}")
    print(f"Encoding: {source.encoding}")


def print_mutations_summary(mutations: list[MutatedSource]) -> None:
    """Print a short summary after default-return mutations are generated."""
    if not mutations:
        return
    first = mutations[0]
    print()
    print("Default-return mutations generated successfully.")
    print(f"Mutation type: {first.mutation_type}")
    print(f"Return type category: {first.return_type_category}")
    print(f"Number of mutations: {len(mutations)}")
    print("Replacement bodies:")
    for mutation in mutations:
        print(f"  - {replacement_return_line(mutation.replacement_body)}")


def print_mutation_tests_summary(results: list[MutationRunResult]) -> None:
    """Print a short summary after all mutation tests complete."""
    pass_count = sum(1 for item in results if item.status == "survived")
    fail_count = sum(1 for item in results if item.status == "killed")
    timeout_count = sum(1 for item in results if item.status == "timeout")

    print()
    print("Mutation tests executed.")
    print(f"Total mutations: {len(results)}")
    print(f"PASS (PT candidate): {pass_count}")
    print(f"FAIL (detected): {fail_count}")
    print(f"TIMEOUT: {timeout_count}")
    print()
    print("Results:")
    for result in results:
        label = replacement_return_line(result.replacement_body)
        exit_display = (
            "timeout"
            if result.timed_out
            else str(result.exit_code)
        )
        status_label = display_status(result.status)
        print(f"  - {label} -> {status_label}, exit code {exit_display}")


def baseline_test_succeeded(baseline: TestRunResult) -> bool:
    """Return True when the baseline test completed successfully (exit code 0)."""
    return not baseline.timed_out and baseline.exit_code == 0


def print_json_result_summary(result: dict[str, Any]) -> None:
    """Print a short summary after the JSON result file is written."""
    print()
    print("JSON result written.")
    print(f"Output file: {result['output_path']}")
    if result.get("mode") == "file_sweep":
        summary = result["summary"]
        table_summary = result.get("table_summary", {})
        print(f"Functions discovered: {result['function_count']}")
        print(f"Processed: {summary['processed']}")
        print(f"Analyzed: {summary['analyzed']}")
        print(f"Skipped: {summary['skipped']}")
        print(f"PASS (PT candidate): {table_summary.get('functions_passed', 0)}")
        rate = table_summary.get("pass_rate_percent")
        if rate is not None:
            print(f"Pass rate: {rate:.1f}%")
        return
    classification = result["classification"]
    print(f"Function classification: {classification['label']}")
    print(f"Survival rate: {classification['survival_rate']:.2f}")


def print_result_table(result: dict[str, Any]) -> None:
    """Print the aligned mutation table and function-level summary."""
    print()
    print(format_result_table(result))


def print_baseline_test_summary(result: TestRunResult) -> None:
    """Print a short summary after the baseline test command runs."""
    print()
    print("Baseline test command executed.")
    print(f"Exit code: {result.exit_code}")
    print(f"Timed out: {result.timed_out}")
    print(f"Runtime: {result.runtime_seconds:.2f} seconds")
    print(f"Stdout characters: {len(result.stdout)}")
    print(f"Stderr characters: {len(result.stderr)}")


def print_location_summary(location: FunctionBodyLocation) -> None:
    """Print a short summary after the function body is located."""
    print()
    print("Function body located successfully.")
    print(f"Function: {location.function_name}")
    print(f"Start line: {location.start_line}")
    print(f"End line: {location.end_line}")
    print(
        "Body range: "
        f"{location.body_start_index}-{location.body_end_index}"
    )


def run_step_validate_input(argv: Sequence[str] | None = None) -> PseudoScopeConfig:
    """
    Step 1: parse CLI arguments and return validated configuration.

    Raises :class:`ConfigError` on invalid input.
    """
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return build_config(
        project_root=args.project_root,
        file=args.file,
        function=args.function,
        test_command=args.test_command,
        output=args.output,
        timeout=args.timeout,
    )


def run_step_read_source(
    config: PseudoScopeConfig,
    *,
    encoding: str = "utf-8",
) -> SourceFile:
    """
    Step 2: read the target source file into memory.

    Raises :class:`SourceReadError` on read or decode failure.
    """
    return read_source_file(config, encoding=encoding)


def run_step_locate_function(
    source: SourceFile,
    function_name: str,
) -> FunctionBodyLocation:
    """
    Step 3: locate the target function body in ``source``.

    Raises :class:`FunctionLocateError` on failure or ambiguity.
    """
    return locate_function_body(source, function_name)


def run_step_generate_mutations(
    source: SourceFile,
    location: FunctionBodyLocation,
) -> list[MutatedSource]:
    """
    Step 4: generate default-return mutations in memory.

    Raises :class:`MutationError` on failure.
    """
    return generate_default_return_mutations(source, location)


def run_step_run_mutation_tests(
    config: PseudoScopeConfig,
    mutations: list[MutatedSource],
) -> list[MutationRunResult]:
    """
    Step 7: run each mutation test (write → test → restore).

    Raises :class:`MutationExecutionError` on failure.
    """
    return run_mutation_tests(config, mutations)


def run_step_write_results(
    config: PseudoScopeConfig,
    baseline: TestRunResult,
    mutation_results: list[MutationRunResult],
    *,
    classification_override: str | None = None,
) -> dict[str, Any]:
    """
    Step 8: build the analysis result and write JSON to ``config.output_path``.

    Raises :class:`ResultWriteError` on write failure.
    """
    result = build_function_analysis_result(
        config,
        baseline,
        mutation_results,
        classification_override=classification_override,
    )
    write_json_result(result, config.output_path)
    return result


def run_step_run_baseline_test(config: PseudoScopeConfig) -> TestRunResult:
    """
    Step 6: run the configured test command once as a baseline.

    Does not write mutated source. Raises :class:`TestRunError` on start failure.
    """
    return run_test_command(config)


def run_file_sweep_mode(config: PseudoScopeConfig) -> int:
    """File sweep: discover all functions, baseline once, analyze each."""
    try:
        source = run_step_read_source(config)
    except SourceReadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print_source_summary(source)

    try:
        discovered = discover_functions(source)
    except DiscoverError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"Discovered {len(discovered)} function(s) in {config.relative_file_path}:")
    for item in discovered:
        print(f"  - {item.name} (line {item.start_line})")

    try:
        result = run_file_sweep(config, source, discovered=discovered)
    except SweepAbortError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ResultWriteError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print_json_result_summary(result)
    print_result_table(result)
    return 0 if result.get("completed", False) else 130


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: validate, read, locate, mutate, baseline test; print summaries."""
    try:
        config = run_step_validate_input(argv)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print_config_summary(config)

    if config.function_name is None:
        return run_file_sweep_mode(config)

    try:
        source = run_step_read_source(config)
    except SourceReadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print_source_summary(source)

    try:
        location = run_step_locate_function(source, config.function_name)
    except FunctionLocateError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print_location_summary(location)

    try:
        mutations = run_step_generate_mutations(source, location)
    except MutationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print_mutations_summary(mutations)

    try:
        baseline = run_step_run_baseline_test(config)
    except TestRunError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print_baseline_test_summary(baseline)

    mutation_results: list[MutationRunResult] = []
    classification_override: str | None = None

    if baseline_test_succeeded(baseline):
        try:
            mutation_results = run_step_run_mutation_tests(config, mutations)
        except MutationExecutionError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print_mutation_tests_summary(mutation_results)
    else:
        classification_override = "baseline_failed"
        print(
            "Warning: Baseline test failed or timed out. "
            "Skipping mutation tests because results would be unreliable.",
            file=sys.stderr,
        )

    try:
        analysis = run_step_write_results(
            config,
            baseline,
            mutation_results,
            classification_override=classification_override,
        )
    except ResultWriteError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print_json_result_summary(analysis)
    print_result_table(analysis)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

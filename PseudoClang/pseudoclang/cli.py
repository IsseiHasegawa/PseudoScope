"""
Command-line interface for PseudoClang.

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
import os
import subprocess
import sys
from typing import Any, Sequence

from pseudoclang.locate import (
    FunctionBodyLocation,
    FunctionLocateError,
    locate_function_body,
)
from pseudoclang.mutate import (
    MutationError,
    MutatedSource,
    generate_default_return_mutations,
    replacement_return_line,
)
from pseudoclang.analysis import execute_plan, resolve_execution_plan
from pseudoclang.coverage_map import (
    CoverageMap,
    CoverageMapError,
    ExecutionPlan,
    PlanKind,
    load_coverage_map,
    verify_project_root,
)
from pseudoclang.executor import (
    STATUS_UNCOMPILABLE,
    MutationExecutionError,
    MutationRunResult,
)
from pseudoclang.models import ConfigError, PseudoScopeConfig
from pseudoclang.preflight import PreflightError, check_test_runner_rebuilds
from pseudoclang.discover import DiscoverError, discover_functions
from pseudoclang.results import (
    ResultWriteError,
    build_function_analysis_result,
    display_status,
    format_result_table,
    write_json_result,
)
from pseudoclang.sweep import SweepAbortError, run_file_sweep
from pseudoclang.runner import TestRunError, TestRunResult, run_test_command
from pseudoclang.source import SourceFile, SourceReadError, read_source_file
from pseudoclang.validation import build_config, require_target_file

DEFAULT_TIMEOUT_SECONDS = 60


COMMANDS: tuple[str, ...] = ("run", "coverage-map", "analyze")


def _add_project_root_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-root-source-dir",
        required=True,
        metavar="PATH",
        help="Root directory of the target project (source tree and test cwd).",
    )


def _add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--file",
        default=None,
        metavar="REL_PATH",
        help=(
            "Source file under the project root, relative to "
            "--project-root-source-dir (optional; required to run analysis)."
        ),
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


def _add_analysis_core_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--test-command",
        required=True,
        metavar="CMD",
        help="Shell command to run tests from the project root.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        metavar="PATH",
        help=(
            "Directory to save output JSON. Relative paths resolve under "
            "--project-root-source-dir. Default: PseudoClang's own output/ "
            "directory (keeps the target project untouched)."
        ),
    )
    parser.add_argument(
        "--output-file",
        default=None,
        metavar="NAME",
        help=(
            "Output JSON file name (optional; default: pseudoclang-results.json). "
            "Used with --output-dir."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=(
            f"Per test-command timeout in seconds "
            f"(optional; default: {DEFAULT_TIMEOUT_SECONDS})."
        ),
    )
    parser.add_argument(
        "--mode",
        default=None,
        metavar="MODE",
        help="Analysis mode (stub; reserved for future use).",
    )
    parser.add_argument(
        "--lang",
        default=None,
        metavar="LANG",
        help="Source language hint (stub; reserved for future use).",
    )


def _add_coverage_map_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--coverage-map",
        default=None,
        metavar="PATH",
        help=(
            "pstrace coverage-map JSON (pstrace-coverage/1). When given, each "
            "function's mutants run only the tests that exercise it, falling "
            "back to --test-command when the map cannot answer. With "
            "--pstrace-module and no explicit path, defaults to PseudoClang's "
            "own output/coverage-map.json (keeps the target project untouched)."
        ),
    )


def _add_consume_args(parser: argparse.ArgumentParser) -> None:
    """Flags that consume an existing coverage map during analysis."""
    parser.add_argument(
        "--assume-coverage-complete",
        action="store_true",
        help=(
            "Treat a function absent from the map as exercised by no test: mark "
            "it survived without running anything (fast, less safe; requires "
            "--coverage-map). Default: fall back to the full --test-command."
        ),
    )
    parser.add_argument(
        "--test-runner-template",
        default=None,
        metavar="CMD",
        help=(
            "Command template used to run a selected subset of tests; must "
            "contain '{selection}', e.g. \"pip install -e . -q && pytest "
            "{selection}\". Without it, selection degrades to full runs."
        ),
    )
    parser.add_argument(
        "--skip-runner-check",
        action="store_true",
        help=(
            "Skip the preflight check that --test-runner-template rebuilds the "
            "target before judging selected mutants (default: run it when a "
            "coverage map and template are both set)."
        ),
    )


def _add_generate_args(parser: argparse.ArgumentParser) -> None:
    """Flags that generate the coverage map (pstrace / a generator command)."""
    parser.add_argument(
        "--coverage-map-cmd",
        default=None,
        metavar="CMD",
        help=(
            "Shell command that generates the --coverage-map JSON (e.g. a pstrace "
            "recipe). Run before the sweep when the map file is absent (or with "
            "--refresh-coverage-map), with $PSEUDOCLANG_COVERAGE_MAP set to the "
            "map's absolute path. Requires --coverage-map."
        ),
    )
    parser.add_argument(
        "--refresh-coverage-map",
        action="store_true",
        help=(
            "Force --coverage-map-cmd to regenerate the map even if the file "
            "already exists (default: reuse an existing map). Requires "
            "--coverage-map-cmd."
        ),
    )

    pstrace = parser.add_argument_group(
        "pstrace integration",
        "Let PseudoClang run pstrace itself to build the coverage map, instead of "
        "running pstrace by hand or writing --coverage-map-cmd. Give --pstrace-module "
        "plus how to build/test the project; PseudoClang generates the map before the "
        "run (pstrace is expected as a sibling checkout, ../pstrace, or via "
        "--pstrace-repo).",
    )
    pstrace.add_argument(
        "--pstrace-module", metavar="MOD",
        help="importable extension module to trace (e.g. ujson). Enables the "
             "pstrace integration; requires --pstrace-src-root/-build-cmd/-test-cmd.",
    )
    pstrace.add_argument(
        "--pstrace-src-root", metavar="PATH",
        help="keep only functions defined under this source tree (relative to "
             "--project-root-source-dir, or absolute).",
    )
    pstrace.add_argument(
        "--pstrace-build-cmd", metavar="CMD",
        help="shell command that builds the extension for tracing (pstrace "
             "instruments it via a compiler wrapper), e.g. 'pip install -e .'.",
    )
    pstrace.add_argument(
        "--pstrace-test-cmd", metavar="CMD",
        help="shell command that runs the pytest suite during tracing, e.g. "
             "'python -m pytest'.",
    )
    pstrace.add_argument(
        "--pstrace-python", metavar="PATH",
        help="the target project's interpreter used to build/test while tracing "
             "(default: the interpreter running PseudoClang).",
    )
    pstrace.add_argument(
        "--pstrace-repo", metavar="PATH",
        help="path to the pstrace checkout (the dir containing the pstrace "
             "package; default: ../pstrace next to this repo).",
    )
    pstrace.add_argument(
        "--pstrace-instrument-path", action="append", default=[], metavar="SUB",
        help="only instrument sources whose path matches SUB (repeatable); for "
             "large multi-extension projects.",
    )
    pstrace.add_argument(
        "--pstrace-hook-in", metavar="SUB",
        help="link the trace hook into only the .so whose name matches SUB "
             "(large multi-extension projects).",
    )
    pstrace.add_argument(
        "--pstrace-hook-mode", choices=["auto", "link", "preload"],
        help="pstrace hook mode (auto = preload on Linux, link on macOS).",
    )


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    """The full analysis argument set (the ``run`` command and the default)."""
    _add_target_args(parser)
    _add_project_root_arg(parser)
    _add_analysis_core_args(parser)
    _add_coverage_map_arg(parser)
    _add_consume_args(parser)
    _add_generate_args(parser)


def build_parser() -> argparse.ArgumentParser:
    """Define PseudoClang CLI arguments, split into stage sub-commands.

    ``run`` (also the implicit default when no sub-command is given) does the
    full pipeline. ``coverage-map`` only builds the pstrace map; ``analyze``
    only runs mutation analysis against an existing map (never regenerates it),
    so an improved test suite can be re-checked without rebuilding the map.
    """
    parser = argparse.ArgumentParser(
        prog="pseudoclang",
        description=(
            "PseudoClang detects pseudo-tested code in C/C++ projects. "
            "Omit --function to analyze every function in --file (file sweep)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example (no sub-command = 'run', the full pipeline):\n"
            "  python -m pseudoclang \\\n"
            "    --project-root-source-dir ultrajson \\\n"
            "    --file src/ujson/python/objToJSON.c \\\n"
            "    --test-command \"source .venv/bin/activate && pip install -e . && pytest\"\n"
            "\n"
            "Sub-commands: run (default), coverage-map (build the map only), "
            "analyze (reuse an existing map, never rebuild it). After improving "
            "tests, re-run only 'analyze' to skip the expensive pstrace step.\n"
            "Results default to PseudoClang's own output/ directory (the target "
            "project is left untouched); pass --output-dir/--output-file to "
            "redirect."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="{run,coverage-map,analyze}",
        help="Stage to run (default: run when omitted).",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Full pipeline: build the coverage map if needed, then analyze.",
    )
    _add_run_arguments(run_parser)

    cov_parser = subparsers.add_parser(
        "coverage-map",
        help="Only (re)build the pstrace coverage map, then exit.",
    )
    _add_project_root_arg(cov_parser)
    _add_coverage_map_arg(cov_parser)
    _add_generate_args(cov_parser)

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Only run mutation analysis, reusing an existing coverage map "
             "(never regenerates it).",
    )
    _add_target_args(analyze_parser)
    _add_project_root_arg(analyze_parser)
    _add_analysis_core_args(analyze_parser)
    _add_coverage_map_arg(analyze_parser)
    _add_consume_args(analyze_parser)

    return parser


def normalize_argv(argv: Sequence[str] | None) -> list[str]:
    """Inject the implicit ``run`` sub-command for backward compatibility.

    Historically the CLI took analysis flags directly (no sub-command). When
    the first token is not a known sub-command (e.g. it is ``--project-root-...``
    or empty), default to ``run`` so old invocations keep working. A top-level
    ``-h``/``--help`` is left alone so the sub-command list is shown.
    """
    args = list(argv) if argv is not None else sys.argv[1:]
    if args and (args[0] in COMMANDS or args[0] in ("-h", "--help")):
        return args
    return ["run", *args]


def _build_config_from_args(args: argparse.Namespace) -> PseudoScopeConfig:
    """Build a :class:`PseudoScopeConfig` from any sub-command's parsed args.

    Sub-commands expose different flag subsets, so missing attributes fall back
    to their defaults via ``getattr``. ``coverage-map`` never runs the mutation
    test command, so ``--test-command`` is not required for it.
    """
    command = getattr(args, "command", "run") or "run"
    return build_config(
        project_root_source_dir=args.project_root_source_dir,
        file=getattr(args, "file", None),
        function=getattr(args, "function", None),
        test_command=getattr(args, "test_command", None),
        output_dir=getattr(args, "output_dir", None),
        output_file=getattr(args, "output_file", None),
        timeout=getattr(args, "timeout", DEFAULT_TIMEOUT_SECONDS),
        mode=getattr(args, "mode", None),
        lang=getattr(args, "lang", None),
        require_test_command=command != "coverage-map",
        coverage_map=getattr(args, "coverage_map", None),
        assume_coverage_complete=getattr(args, "assume_coverage_complete", False),
        test_runner_template=getattr(args, "test_runner_template", None),
        coverage_map_cmd=getattr(args, "coverage_map_cmd", None),
        refresh_coverage_map=getattr(args, "refresh_coverage_map", False),
        skip_runner_check=getattr(args, "skip_runner_check", False),
        pstrace_module=getattr(args, "pstrace_module", None),
        pstrace_src_root=getattr(args, "pstrace_src_root", None),
        pstrace_build_cmd=getattr(args, "pstrace_build_cmd", None),
        pstrace_test_cmd=getattr(args, "pstrace_test_cmd", None),
        pstrace_python=getattr(args, "pstrace_python", None),
        pstrace_repo=getattr(args, "pstrace_repo", None),
        pstrace_instrument_path=getattr(args, "pstrace_instrument_path", None),
        pstrace_hook_in=getattr(args, "pstrace_hook_in", None),
        pstrace_hook_mode=getattr(args, "pstrace_hook_mode", None),
    )


def print_config_summary(config: PseudoScopeConfig) -> None:
    """Print a human-readable summary of the validated configuration."""
    print("PseudoClang configuration loaded successfully.")
    print()
    print(f"Project root: {config.project_root}")
    if config.target_file is not None:
        print(f"Target file: {config.target_file}")
    if config.function_name:
        print(f"Function: {config.function_name}")
    elif config.target_file is not None:
        print("Mode: file sweep (all functions in target file)")
    if config.mode is not None:
        print(f"CLI mode (stub): {config.mode}")
    if config.lang is not None:
        print(f"Language hint (stub): {config.lang}")
    print(f"Test command: {config.test_command}")
    print(f"Output file: {config.output_path}")
    print(f"Timeout: {config.timeout_seconds} seconds")
    if config.coverage_map_path is not None:
        print(f"Coverage map: {config.coverage_map_path}")
        print(f"Assume coverage complete: {config.assume_coverage_complete}")
        if config.test_runner_template is not None:
            print(f"Test runner template: {config.test_runner_template}")


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
    uncompilable_count = sum(
        1 for item in results if item.status == STATUS_UNCOMPILABLE
    )

    print()
    print("Mutation tests executed.")
    print(f"Total mutations: {len(results)}")
    print(f"PASS (PT candidate): {pass_count}")
    print(f"FAIL (detected): {fail_count}")
    print(f"TIMEOUT: {timeout_count}")
    print(f"UNCOMPILABLE (skipped): {uncompilable_count}")
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
        _print_selection_summary(result.get("selection"))
        return
    classification = result["classification"]
    print(f"Function classification: {classification['label']}")
    print(f"Survival rate: {classification['survival_rate']:.2f}")


def _print_selection_summary(selection: dict[str, Any] | None) -> None:
    """Print coverage-map provenance counts and the test-execution savings."""
    if not selection:
        return
    print()
    print("Test selection (coverage map)")
    judgments = selection.get("judgments", {})
    for judgment in sorted(judgments):
        print(f"  {judgment}: {judgments[judgment]}")
    with_map = selection.get("tests_executed_estimate")
    without_map = selection.get("tests_executed_estimate_without_map")
    saved = selection.get("estimated_tests_saved")
    if with_map is not None and without_map is not None:
        print(
            f"  Estimated test runs: {with_map} with map "
            f"vs {without_map} without (saved {saved})"
        )


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

    Parses in the implicit-``run`` form (no sub-command needed), so existing
    callers keep working. Raises :class:`ConfigError` on invalid input.
    """
    parser = build_parser()
    args = parser.parse_args(normalize_argv(argv))
    return _build_config_from_args(args)


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
    *,
    execution_plan: ExecutionPlan,
) -> list[MutationRunResult]:
    """
    Step 7: run each mutation test (write → test → restore).

    ``execution_plan`` selects the test scope (a coverage-driven subset, the
    full suite, or skip-as-survived). Raises :class:`MutationExecutionError` on
    failure.
    """
    return execute_plan(config, mutations, execution_plan)


def run_step_write_results(
    config: PseudoScopeConfig,
    baseline: TestRunResult,
    mutation_results: list[MutationRunResult],
    *,
    classification_override: str | None = None,
    judgment: str | None = None,
    selected_tests: tuple[str, ...] | None = None,
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
        judgment=judgment,
        selected_tests=selected_tests,
    )
    write_json_result(result, config.output_path)
    return result


def run_step_run_baseline_test(config: PseudoScopeConfig) -> TestRunResult:
    """
    Step 6: run the configured test command once as a baseline.

    Does not write mutated source. Raises :class:`TestRunError` on start failure.
    """
    return run_test_command(config)


def generate_coverage_map(config: PseudoScopeConfig) -> None:
    """
    Run ``--coverage-map-cmd`` to produce the map at ``config.coverage_map_path``.

    The command inherits this process's working directory and gets
    ``$PSEUDOCLANG_COVERAGE_MAP`` set to the map's absolute path, so a generator
    (e.g. a pstrace recipe) can write exactly where PseudoClang reads. Its output
    streams to the console. A non-zero exit or a missing output file is fatal.
    """
    out_path = config.coverage_map_path
    assert config.coverage_map_cmd is not None and out_path is not None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PSEUDOCLANG_COVERAGE_MAP": str(out_path)}

    print()
    print(f"Generating coverage map via --coverage-map-cmd -> {out_path}")
    print(f"  $ {config.coverage_map_cmd}")
    completed = subprocess.run(config.coverage_map_cmd, shell=True, env=env)
    if completed.returncode != 0:
        raise CoverageMapError(
            f"--coverage-map-cmd failed (exit {completed.returncode}); "
            "coverage map was not generated."
        )
    if not out_path.exists():
        raise CoverageMapError(
            f"--coverage-map-cmd exited 0 but produced no map at {out_path}. "
            "Ensure the command writes to $PSEUDOCLANG_COVERAGE_MAP."
        )


def load_coverage_map_for_run(config: PseudoScopeConfig) -> CoverageMap | None:
    """
    Load and validate the coverage map (if any) and run staleness checks.

    Returns the map, or ``None`` when no ``--coverage-map`` was given. Raises
    :class:`CoverageMapError` on a bad map or a project-root mismatch.

    When ``--coverage-map-cmd`` is set, the map is generated first if its file is
    absent (or ``--refresh-coverage-map`` was given), otherwise the existing file
    is reused.
    """
    if config.coverage_map_cmd is not None and config.coverage_map_path is not None:
        if config.refresh_coverage_map or not config.coverage_map_path.exists():
            generate_coverage_map(config)
        else:
            print()
            print(
                f"Reusing existing coverage map: {config.coverage_map_path} "
                "(pass --refresh-coverage-map to regenerate)."
            )

    coverage_map = load_coverage_map(config.coverage_map_path)
    if coverage_map is None:
        return None

    verify_project_root(coverage_map, config.project_root)

    print()
    print(f"Coverage map loaded: {config.coverage_map_path}")
    print(f"  schema: pstrace-coverage/1, tests universe: {len(coverage_map.universe())}")
    if config.test_runner_template is None:
        print(
            "Warning: --coverage-map provided without --test-runner-template; "
            "test selection is disabled and the full --test-command will run "
            "per mutant (absent functions may still be skipped with "
            "--assume-coverage-complete).",
            file=sys.stderr,
        )
    return coverage_map


def run_file_sweep_mode(
    config: PseudoScopeConfig,
    coverage_map: CoverageMap | None = None,
) -> int:
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
        result = run_file_sweep(
            config, source, discovered=discovered, coverage_map=coverage_map
        )
    except SweepAbortError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ResultWriteError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print_json_result_summary(result)
    print_result_table(result)
    return 0 if result.get("completed", False) else 130


def _run_analysis(config: PseudoScopeConfig) -> int:
    """Run mutation analysis for a validated config (the run/analyze stages).

    The coverage map is loaded via :func:`load_coverage_map_for_run`, which only
    (re)generates it when ``config.coverage_map_cmd`` is set. The ``analyze``
    stage never sets it, so it always reuses an existing map (never rebuilds).
    """
    print_config_summary(config)

    try:
        coverage_map = load_coverage_map_for_run(config)
    except CoverageMapError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if (
        coverage_map is not None
        and config.test_runner_template is not None
        and not config.skip_runner_check
    ):
        try:
            check_test_runner_rebuilds(config, coverage_map)
        except PreflightError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    if config.function_name is None:
        return run_file_sweep_mode(config, coverage_map)

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
    plan: ExecutionPlan | None = None

    if baseline_test_succeeded(baseline):
        plan = resolve_execution_plan(config, coverage_map, config.function_name)
        try:
            mutation_results = run_step_run_mutation_tests(
                config, mutations, execution_plan=plan
            )
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

    selected_tests = (
        plan.nodeids
        if plan is not None and plan.kind is PlanKind.RUN_SELECTED
        else None
    )
    try:
        analysis = run_step_write_results(
            config,
            baseline,
            mutation_results,
            classification_override=classification_override,
            judgment=plan.judgment if plan is not None else None,
            selected_tests=selected_tests,
        )
    except ResultWriteError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print_json_result_summary(analysis)
    print_result_table(analysis)
    return 0


def _run_coverage_map_command(args: argparse.Namespace) -> int:
    """The ``coverage-map`` stage: (re)build the pstrace map only, then exit."""
    try:
        config = _build_config_from_args(args)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if config.coverage_map_cmd is None or config.coverage_map_path is None:
        print(
            "Error: coverage-map needs a generator: pass --pstrace-module with "
            "its sub-flags, or --coverage-map-cmd together with --coverage-map.",
            file=sys.stderr,
        )
        return 1

    print(f"Project root: {config.project_root}")
    print(f"Coverage map: {config.coverage_map_path}")

    if config.coverage_map_path.exists() and not config.refresh_coverage_map:
        print()
        print(
            f"Coverage map already exists: {config.coverage_map_path} "
            "(pass --refresh-coverage-map to regenerate)."
        )
        return 0

    try:
        generate_coverage_map(config)
    except CoverageMapError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"Coverage map written: {config.coverage_map_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: dispatch a stage sub-command (default: ``run``).

    ``run`` (and the implicit default when no sub-command is given) does the
    full pipeline. ``coverage-map`` only builds the pstrace map. ``analyze``
    only re-runs mutation analysis against an existing map, so an improved test
    suite can be re-checked without the expensive map rebuild.
    """
    parser = build_parser()
    args = parser.parse_args(normalize_argv(argv))
    command = getattr(args, "command", "run") or "run"

    if command == "coverage-map":
        return _run_coverage_map_command(args)

    # run / analyze both run mutation analysis; they differ only in flags, so
    # analyze has no map-generation options and always reuses an existing map.
    try:
        config = _build_config_from_args(args)
        require_target_file(config)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return _run_analysis(config)


if __name__ == "__main__":
    raise SystemExit(main())

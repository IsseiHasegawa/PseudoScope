"""
Command-line interface for PseudoScope.

Step 1: parse and validate CLI input.
Step 2: read the target source file into memory.
Step 3: locate the target function body range.
Step 4: generate default-return mutations in memory.

This module does not write files to disk, run tests, or write JSON.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

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
from pseudoscope.models import ConfigError, PseudoScopeConfig
from pseudoscope.source import SourceFile, SourceReadError, read_source_file
from pseudoscope.validation import build_config


def build_parser() -> argparse.ArgumentParser:
    """Define PseudoScope CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="pseudoscope",
        description=(
            "PseudoScope detects pseudo-tested code in C/C++ projects. "
            "Current release: Steps 1–4 — validate input, read source, "
            "locate the function body, and generate default-return mutations."
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
        required=True,
        metavar="NAME",
        help="Name of the function or method to analyze in the target file.",
    )
    parser.add_argument(
        "--test-command",
        required=True,
        metavar="CMD",
        help="Shell command to run tests from the project root (validated now, not executed).",
    )
    parser.add_argument(
        "--output",
        default="pseudoscope-results.json",
        metavar="PATH",
        help=(
            "Planned JSON output path (default: pseudoscope-results.json). "
            "Resolved now; the file is not created yet."
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
    print(f"Function: {config.function_name}")
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


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: validate, read, locate, mutate; print summaries."""
    try:
        config = run_step_validate_input(argv)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print_config_summary(config)

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
File sweep: discover functions, baseline once, analyze each function.

Writes the final JSON only when the sweep completes successfully. Maintains a
hidden partial JSON file during the run for best-effort recovery on interrupt.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path
from typing import Any

from pseudoclang.analysis import FunctionAnalysisOutcome, analyze_function
from pseudoclang.coverage_map import CoverageMap
from pseudoclang.discover import DiscoverError, DiscoveredFunction, discover_functions
from pseudoclang.models import PseudoScopeConfig
from pseudoclang.results import (
    ResultWriteError,
    build_file_sweep_result,
    partial_output_path,
    write_json_result,
)
from pseudoclang.runner import TestRunError, TestRunResult, run_test_command
from pseudoclang.source import SourceFile


class SweepAbortError(Exception):
    """Raised when the sweep must stop (e.g. failed source restore)."""


def baseline_test_succeeded(baseline: TestRunResult) -> bool:
    return not baseline.timed_out and baseline.exit_code == 0


def run_file_sweep(
    config: PseudoScopeConfig,
    source: SourceFile,
    *,
    discovered: list[DiscoveredFunction] | None = None,
    coverage_map: CoverageMap | None = None,
) -> dict[str, Any]:
    """
    Run baseline once, then analyze each discovered function.

    Returns the final JSON-serializable result dict. Writes ``config.output_path``
    only on successful completion. Updates a partial JSON file after each function.

    When ``coverage_map`` is provided, each function is tested via its
    coverage-driven plan; otherwise behavior is unchanged (full suite per mutant).
    """
    if discovered is None:
        discovered = discover_functions(source)

    if coverage_map is not None and not coverage_map.has_file(
        config.relative_file_path
    ):
        print(
            f"Warning: coverage map has no data for {config.relative_file_path}; "
            "every function will fall back to the full --test-command. The file "
            "may not have been built with instrumentation or exercised by tests.",
            file=sys.stderr,
        )

    partial_path = partial_output_path(config.output_path)
    interrupted = False

    def _on_interrupt(signum: int, frame: object | None) -> None:
        nonlocal interrupted
        interrupted = True
        print(
            "\nInterrupt received. Finishing current function, then saving "
            "best-effort partial results.",
            file=sys.stderr,
        )

    previous_handler = signal.signal(signal.SIGINT, _on_interrupt)

    try:
        try:
            baseline = run_test_command(config)
        except TestRunError as exc:
            raise SweepAbortError(f"Baseline test failed to start: {exc}") from exc

        run_mutations = baseline_test_succeeded(baseline)
        if not run_mutations:
            print(
                "Warning: Baseline test failed or timed out. "
                "Skipping mutation tests for all functions.",
                file=sys.stderr,
            )

        outcomes: list[FunctionAnalysisOutcome] = []

        def _build_result(*, completed: bool) -> dict[str, Any]:
            return build_file_sweep_result(
                config,
                source,
                discovered,
                baseline,
                outcomes,
                completed=completed,
                coverage_map=coverage_map,
            )

        for index, item in enumerate(discovered, start=1):
            if interrupted:
                break

            print()
            print(f"[{index}/{len(discovered)}] Function: {item.name} (line {item.start_line})")

            outcome = analyze_function(
                config,
                source,
                item.name,
                run_mutations=run_mutations,
                coverage_map=coverage_map,
            )
            outcomes.append(outcome)

            if outcome.critical_error:
                print(f"Error: {outcome.critical_error}", file=sys.stderr)
                partial = _build_result(completed=False)
                write_json_result(partial, partial_path)
                raise SweepAbortError(outcome.critical_error)

            _print_function_outcome(outcome)

            partial = _build_result(completed=False)
            write_json_result(partial, partial_path)

        if interrupted:
            partial = _build_result(completed=False)
            write_json_result(partial, partial_path)
            _try_promote_partial(partial_path, config.output_path)
            return partial

        result = _build_result(completed=True)
        write_json_result(result, config.output_path)
        if partial_path.exists():
            partial_path.unlink()
        return result
    finally:
        signal.signal(signal.SIGINT, previous_handler)


def _try_promote_partial(partial_path: Path, output_path: Path) -> None:
    try:
        if partial_path.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                partial_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            print(
                f"Partial results copied to {output_path}",
                file=sys.stderr,
            )
    except OSError as exc:
        print(
            f"Could not copy partial results to {output_path}: {exc}. "
            f"See {partial_path}",
            file=sys.stderr,
        )


def _print_function_outcome(outcome: FunctionAnalysisOutcome) -> None:
    if outcome.status == "skipped":
        print(f"  Skipped ({outcome.reason})")
        return
    if not outcome.mutation_results:
        print("  No mutation results")
        return
    survived = sum(1 for item in outcome.mutation_results if item.status == "survived")
    killed = sum(1 for item in outcome.mutation_results if item.status == "killed")
    print(f"  Mutations: {len(outcome.mutation_results)} "
          f"(PASS: {survived}, FAIL: {killed})")

"""
Build classification, result table, and write JSON results (Step 8).

Uses internal mutation statuses (survived, killed, timeout) unchanged in JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pseudoclang.analysis import FunctionAnalysisOutcome
from pseudoclang.coverage_map import (
    JUDGMENT_SELECTED,
    JUDGMENT_SKIPPED_UNCOVERED,
    CoverageMap,
)
from pseudoclang.discover import DiscoveredFunction
from pseudoclang.executor import STATUS_UNCOMPILABLE, MutationRunResult
from pseudoclang.models import PseudoScopeConfig
from pseudoclang.mutate import replacement_return_line
from pseudoclang.runner import TestRunResult
from pseudoclang.source import SourceFile


class ResultWriteError(Exception):
    """Raised when the JSON result file cannot be written."""


def classify_function(mutation_results: list[MutationRunResult]) -> str:
    """
    Classify the target function from mutation test outcomes.

    Returns one of: ``not_analyzed``, ``inconclusive_timeout``,
    ``pseudo_tested_candidate``, ``not_pseudo_tested``, ``partially_tested``.
    """
    # Uncompilable mutants cannot be judged; exclude them from classification
    # (and the score denominator) rather than counting them as killed.
    scored = [r for r in mutation_results if r.status != STATUS_UNCOMPILABLE]
    if not scored:
        return "not_analyzed"

    if any(result.status == "timeout" for result in scored):
        return "inconclusive_timeout"

    statuses = {result.status for result in scored}
    if statuses == {"survived"}:
        return "pseudo_tested_candidate"
    if statuses == {"killed"}:
        return "not_pseudo_tested"
    if "survived" in statuses and "killed" in statuses:
        return "partially_tested"

    return "partially_tested"


def display_status(status: str) -> str:
    """Map internal mutation test status to a user-facing label."""
    labels = {
        "survived": "SURVIVED (PT)",
        "killed": "KILLED",
        "timeout": "TIMEOUT",
        STATUS_UNCOMPILABLE: "UNCOMPILABLE (skipped)",
    }
    return labels.get(status, status)


def _exit_display(result: MutationRunResult) -> str:
    if result.timed_out:
        return "timeout"
    return "-" if result.exit_code is None else str(result.exit_code)


def mutant_detail_lines(
    result: MutationRunResult, *, level: int, tail: int = 20
) -> list[str]:
    """Verbose per-mutant lines for a given verbosity ``level``.

    ``level >= 2`` (verbose) adds one line with the mutant's exit code, runtime,
    and status. ``level >= 3`` (trace) also adds the exact command run and the
    last ``tail`` lines of its captured stdout/stderr. Returns an empty list
    below verbose so callers can print unconditionally. Pure (no I/O).
    """
    from pseudoclang.reporting import tail_lines

    lines: list[str] = []
    if level < 2:
        return lines

    label = replacement_return_line(result.replacement_body)
    lines.append(
        f"      {label} -> {display_status(result.status)} "
        f"(exit {_exit_display(result)}, {result.runtime_seconds:.2f}s)"
    )
    if level < 3:
        return lines

    if result.test_command:
        lines.append(f"        $ {result.test_command}")
    for name, text in (("stdout", result.stdout), ("stderr", result.stderr)):
        shown, total = tail_lines(text, tail)
        if not shown:
            continue
        clipped = "" if total <= len(shown) else f" (last {len(shown)} of {total})"
        lines.append(f"        {name}{clipped}:")
        lines.extend(f"          {line}" for line in shown)
    return lines


_TABLE_COLUMN_KEYS = ("file_path", "function", "mutant", "test_result")
_TABLE_HEADERS = ("File", "Function", "Mutant", "Test result")


def compute_function_table_summary(result: dict[str, Any]) -> dict[str, Any]:
    """
    Function-level counts for the results table footer.

    *Functions analyzed* = functions that ran mutation tests.
    *SURVIVED (PT)* = all mutations survived for that function.
    """
    if result.get("mode") == "file_sweep":
        sweep = result.get("summary", {})
        discovered = int(result.get("function_count", sweep.get("discovered", 0)))
        analyzed = int(sweep.get("analyzed", 0))
        passed = int(sweep.get("pseudo_tested_candidates", 0))
    else:
        discovered = 1
        label = result.get("classification", {}).get("label", "not_analyzed")
        if result.get("mutations"):
            analyzed = 1
            passed = 1 if label == "pseudo_tested_candidate" else 0
        else:
            analyzed = 0
            passed = 0

    pass_rate_percent: float | None
    if analyzed:
        pass_rate_percent = round(passed / analyzed * 100.0, 1)
    else:
        pass_rate_percent = None

    return {
        "functions_discovered": discovered,
        "functions_analyzed": analyzed,
        "functions_passed": passed,
        "pass_rate_percent": pass_rate_percent,
    }


def _truncate_cell(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if len(text) <= max_width:
        return text
    if max_width <= 3:
        return text[:max_width]
    return f"{text[: max_width - 3]}..."


def _display_file_path(file_path: str) -> str:
    return Path(file_path).name or file_path


def _column_widths(rows: list[dict[str, str]], keys: tuple[str, ...]) -> list[int]:
    widths = [len(header) for header in _TABLE_HEADERS]
    for row in rows:
        for index, key in enumerate(keys):
            widths[index] = max(widths[index], len(row.get(key, "")))
    return [min(width, cap) for width, cap in zip(widths, (36, 32, 20, 22), strict=True)]


def _format_table_row(cells: tuple[str, ...], widths: list[int]) -> str:
    parts = [
        _truncate_cell(cell, width).ljust(width)
        for cell, width in zip(cells, widths, strict=True)
    ]
    return "  ".join(parts)


def format_result_table(result: dict[str, Any]) -> str:
    """Return an aligned mutation table with a function-level summary footer."""
    rows = result.get("table_rows", [])
    summary = result.get("table_summary") or compute_function_table_summary(result)

    lines: list[str] = []
    lines.append("Mutation results")
    lines.append("")

    if rows:
        display_rows: list[dict[str, str]] = []
        for row in rows:
            display_rows.append(
                {
                    "file_path": _display_file_path(row["file_path"]),
                    "function": row["function"],
                    "mutant": row["mutant"],
                    "test_result": row["test_result"],
                }
            )
        widths = _column_widths(display_rows, _TABLE_COLUMN_KEYS)
        separator = "  ".join("-" * width for width in widths)

        lines.append(_format_table_row(_TABLE_HEADERS, widths))
        lines.append(separator)
        for row in display_rows:
            lines.append(
                _format_table_row(
                    tuple(row[key] for key in _TABLE_COLUMN_KEYS),
                    widths,
                )
            )
        lines.append(separator)
    else:
        lines.append("(no mutation test rows)")
        lines.append("-" * 72)

    lines.append("")
    lines.append("Summary (by function)")
    lines.append(f"  Discovered          : {summary['functions_discovered']}")
    lines.append(f"  Analyzed (tested)   : {summary['functions_analyzed']}")
    lines.append(f"  SURVIVED (PT)       : {summary['functions_passed']}")
    rate = summary.get("pass_rate_percent")
    if rate is None:
        lines.append("  Pass rate           : n/a (no functions analyzed)")
    else:
        lines.append(f"  Pass rate           : {rate:.1f}%")
    return "\n".join(lines)


def attach_table_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Add ``table_summary`` for JSON output and CLI formatting."""
    result["table_summary"] = compute_function_table_summary(result)
    return result


def build_result_table_rows(
    config: PseudoScopeConfig,
    mutation_results: list[MutationRunResult],
) -> list[dict[str, str]]:
    """Build compact table rows for CLI and JSON output."""
    file_path = str(config.relative_file_path)
    rows: list[dict[str, str]] = []
    for result in mutation_results:
        rows.append(
            {
                "file_path": file_path,
                "function": result.function_name,
                "mutant": replacement_return_line(result.replacement_body),
                "test_result": display_status(result.status),
            }
        )
    return rows


def _baseline_payload(baseline: TestRunResult) -> dict[str, Any]:
    return {
        "exit_code": baseline.exit_code,
        "timed_out": baseline.timed_out,
        "runtime_seconds": baseline.runtime_seconds,
        "stdout": baseline.stdout,
        "stderr": baseline.stderr,
    }


def _mutation_payload(result: MutationRunResult) -> dict[str, Any]:
    mutant = replacement_return_line(result.replacement_body)
    return {
        "function_name": result.function_name,
        "mutation_type": result.mutation_type,
        "return_type_category": result.return_type_category,
        "replacement_body": result.replacement_body,
        "mutant": mutant,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "runtime_seconds": result.runtime_seconds,
        "status": result.status,
        "display_status": display_status(result.status),
        "restored": result.restored,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _selection_payload(
    judgment: str | None,
    selected_tests: tuple[str, ...] | None,
    mutation_results: list[MutationRunResult],
) -> dict[str, Any]:
    """How a function's mutants were judged via the coverage map (provenance).

    ``mutation_runs`` is the number of mutant test invocations actually
    executed: zero for a skip-as-survived verdict (no test ran), otherwise one
    per mutant.
    """
    skipped = judgment == JUDGMENT_SKIPPED_UNCOVERED
    return {
        "judgment": judgment,
        "selected_test_count": len(selected_tests) if selected_tests else None,
        "selected_tests": list(selected_tests) if selected_tests else None,
        "mutation_runs": 0 if skipped else len(mutation_results),
    }


def build_selection_summary(
    outcomes: list[FunctionAnalysisOutcome],
    universe_size: int,
) -> dict[str, Any]:
    """
    Aggregate coverage-map provenance and the test-execution savings.

    ``tests_executed_estimate`` counts mutant-test invocations using each
    function's selected nodeid count (selected), the suite size (full fallback),
    or zero (skipped). ``..._without_map`` is the same workload run as the full
    suite for every mutant. The difference shows where selection helped. The
    suite size is approximated by the map's passing-tests universe.
    """
    judgments: dict[str, int] = {}
    selected_invocations = 0
    with_map = 0
    without_map = 0
    for outcome in outcomes:
        if outcome.judgment is None:
            continue
        judgments[outcome.judgment] = judgments.get(outcome.judgment, 0) + 1
        mutants = len(outcome.mutation_results)
        without_map += mutants * universe_size
        if outcome.judgment == JUDGMENT_SELECTED and outcome.selected_tests:
            invocations = mutants * len(outcome.selected_tests)
            selected_invocations += invocations
            with_map += invocations
        elif outcome.judgment == JUDGMENT_SKIPPED_UNCOVERED:
            pass  # nothing executed
        else:
            with_map += mutants * universe_size

    return {
        "universe_size": universe_size,
        "judgments": judgments,
        "selected_test_invocations": selected_invocations,
        "tests_executed_estimate": with_map,
        "tests_executed_estimate_without_map": without_map,
        "estimated_tests_saved": without_map - with_map,
    }


def _classification_payload(
    mutation_results: list[MutationRunResult],
    *,
    label: str,
) -> dict[str, Any]:
    total = len(mutation_results)
    survived = sum(1 for item in mutation_results if item.status == "survived")
    killed = sum(1 for item in mutation_results if item.status == "killed")
    timeout = sum(1 for item in mutation_results if item.status == "timeout")
    uncompilable = sum(
        1 for item in mutation_results if item.status == STATUS_UNCOMPILABLE
    )
    # Uncompilable mutants are excluded from the mutation-score denominator.
    scored = total - uncompilable
    survival_rate = (survived / scored) if scored else 0.0

    return {
        "label": label,
        "survival_rate": survival_rate,
        "total_mutations": total,
        "scored_mutations": scored,
        "survived": survived,
        "killed": killed,
        "timeout": timeout,
        "uncompilable": uncompilable,
    }


def build_function_analysis_result(
    config: PseudoScopeConfig,
    baseline: TestRunResult,
    mutation_results: list[MutationRunResult],
    *,
    classification_override: str | None = None,
    judgment: str | None = None,
    selected_tests: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable analysis result for one function."""
    label = (
        classification_override
        if classification_override is not None
        else classify_function(mutation_results)
    )
    table_rows = build_result_table_rows(config, mutation_results)

    result: dict[str, Any] = {
        "project_root": str(config.project_root),
        "file": str(config.relative_file_path),
        "function": config.function_name,
        "cli_mode": config.mode,
        "lang": config.lang,
        "test_command": config.test_command,
        "output_path": str(config.output_path),
        "baseline": _baseline_payload(baseline),
        "classification": _classification_payload(
            mutation_results,
            label=label,
        ),
        "mutations": [_mutation_payload(item) for item in mutation_results],
        "table_rows": table_rows,
    }
    # Only surface provenance when a map was used (byte-identical output without).
    if config.coverage_map_path is not None:
        result["selection"] = _selection_payload(
            judgment, selected_tests, mutation_results
        )
    return attach_table_summary(result)


def partial_output_path(output_path: Path) -> Path:
    """Hidden partial JSON path used during file sweep (best-effort on interrupt)."""
    return output_path.parent / f".{output_path.name}.pseudoclang-sweep.partial"


def build_function_entry(
    config: PseudoScopeConfig,
    outcome: FunctionAnalysisOutcome,
) -> dict[str, Any]:
    """Build one function entry for file-sweep JSON output."""
    label = (
        outcome.classification_override
        if outcome.classification_override is not None
        else classify_function(outcome.mutation_results)
    )
    entry: dict[str, Any] = {
        "function": outcome.function_name,
        "status": outcome.status,
        "reason": outcome.reason,
        "start_line": outcome.start_line,
        "end_line": outcome.end_line,
        "classification": _classification_payload(
            outcome.mutation_results,
            label=label,
        ),
        "mutations": [_mutation_payload(item) for item in outcome.mutation_results],
    }
    # Only surface coverage-driven provenance when a map was actually used, so a
    # run without --coverage-map produces byte-identical output to before.
    if config.coverage_map_path is not None:
        entry["selection"] = _selection_payload(
            outcome.judgment, outcome.selected_tests, outcome.mutation_results
        )
    if outcome.critical_error:
        entry["critical_error"] = outcome.critical_error
    return entry


def build_sweep_summary(
    discovered_count: int,
    outcomes: list[FunctionAnalysisOutcome],
) -> dict[str, Any]:
    """Aggregate counts for a file sweep."""
    analyzed = [item for item in outcomes if item.status == "analyzed"]
    skipped = [item for item in outcomes if item.status == "skipped"]
    labels = [
        (
            item.classification_override
            if item.classification_override is not None
            else classify_function(item.mutation_results)
        )
        for item in analyzed
    ]
    return {
        "discovered": discovered_count,
        "processed": len(outcomes),
        "analyzed": len(analyzed),
        "skipped": len(skipped),
        "pseudo_tested_candidates": sum(
            1 for label in labels if label == "pseudo_tested_candidate"
        ),
        "not_pseudo_tested": sum(
            1 for label in labels if label == "not_pseudo_tested"
        ),
        "partially_tested": sum(
            1 for label in labels if label == "partially_tested"
        ),
        "inconclusive_timeout": sum(
            1 for label in labels if label == "inconclusive_timeout"
        ),
        "baseline_failed": sum(
            1 for item in skipped if item.reason == "baseline_failed"
        ),
    }


def build_file_sweep_result(
    config: PseudoScopeConfig,
    source: SourceFile,
    discovered: list[DiscoveredFunction],
    baseline: TestRunResult,
    outcomes: list[FunctionAnalysisOutcome],
    *,
    completed: bool,
    coverage_map: CoverageMap | None = None,
) -> dict[str, Any]:
    """Build JSON-serializable file-sweep results."""
    function_entries = [
        build_function_entry(config, outcome) for outcome in outcomes
    ]
    all_mutation_results: list[MutationRunResult] = []
    for outcome in outcomes:
        all_mutation_results.extend(outcome.mutation_results)

    table_rows = build_result_table_rows(config, all_mutation_results)
    baseline_ok = not baseline.timed_out and baseline.exit_code == 0

    result: dict[str, Any] = {
        "mode": "file_sweep",
        "completed": completed,
        "project_root": str(config.project_root),
        "file": str(config.relative_file_path),
        "cli_mode": config.mode,
        "lang": config.lang,
        "test_command": config.test_command,
        "output_path": str(config.output_path),
        "baseline": _baseline_payload(baseline),
        "baseline_succeeded": baseline_ok,
        "function_count": len(discovered),
        "functions_discovered": [item.name for item in discovered],
        "functions": function_entries,
        "summary": build_sweep_summary(len(discovered), outcomes),
        "table_rows": table_rows,
    }
    if coverage_map is not None:
        result["coverage_map_path"] = (
            str(config.coverage_map_path) if config.coverage_map_path else None
        )
        result["selection"] = build_selection_summary(
            outcomes, len(coverage_map.universe())
        )
    return attach_table_summary(result)


def write_json_result(result: dict[str, Any], output_path: Path) -> None:
    """Write ``result`` as pretty-printed UTF-8 JSON to ``output_path``."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(result, indent=2, ensure_ascii=False)
        output_path.write_text(f"{payload}\n", encoding="utf-8")
    except OSError as exc:
        raise ResultWriteError(
            f"Failed to write JSON result to {output_path}: {exc}"
        ) from exc
    except (TypeError, ValueError) as exc:
        raise ResultWriteError(
            f"Failed to encode JSON result for {output_path}: {exc}"
        ) from exc

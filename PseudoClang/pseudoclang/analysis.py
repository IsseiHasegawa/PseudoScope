"""
Analyze a single function (locate → mutate → optional mutation tests).

Shared by single-function CLI mode and file sweep mode.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from pseudoclang.coverage_map import (
    JUDGMENT_FULL_NO_MAP,
    JUDGMENT_FULL_NO_TEMPLATE,
    CoverageMap,
    ExecutionPlan,
    PlanKind,
    decide_execution,
)
from pseudoclang.executor import (
    STATUS_UNCOMPILABLE,
    MutationExecutionError,
    MutationRunResult,
    run_mutation_tests,
    survived_without_running,
)
from pseudoclang.locate import FunctionBodyLocation, FunctionLocateError, locate_function_body
from pseudoclang.models import PseudoScopeConfig
from pseudoclang.mutate import (
    MutationError,
    MutatedSource,
    UnsupportedReturnTypeError,
    generate_default_return_mutations,
)
from pseudoclang.runner import TestRunResult
from pseudoclang.source import SourceFile


@dataclass(frozen=True)
class FunctionAnalysisOutcome:
    """Outcome of analyzing one function (success, skip, or critical failure)."""

    function_name: str
    status: str
    reason: str | None
    start_line: int | None
    end_line: int | None
    location: FunctionBodyLocation | None
    mutation_results: list[MutationRunResult]
    classification_override: str | None
    critical_error: str | None = None
    # How the mutants were judged (coverage-map provenance) and, for a selected
    # run, which tests were used. None on functions skipped before judging.
    judgment: str | None = None
    selected_tests: tuple[str, ...] | None = None


def resolve_execution_plan(
    config: PseudoScopeConfig,
    coverage_map: CoverageMap | None,
    function_name: str,
) -> ExecutionPlan:
    """
    Decide how to test ``function_name``'s mutants and record the provenance.

    Layers the orchestration concerns on top of the pure
    :func:`decide_execution` policy: no-map handling, a stale-map warning when
    selected nodeids fall outside the map's universe, and degrading a selected
    plan to a full run when no ``--test-runner-template`` is configured.
    """
    if coverage_map is None:
        return ExecutionPlan(PlanKind.RUN_FULL, judgment=JUDGMENT_FULL_NO_MAP)

    selection = coverage_map.lookup(config.relative_file_path, function_name)
    plan = decide_execution(
        selection, assume_complete=config.assume_coverage_complete
    )

    if plan.kind is PlanKind.RUN_SELECTED:
        unknown = [n for n in plan.nodeids if n not in coverage_map.universe()]
        if unknown:
            print(
                f"Warning: coverage map lists {len(unknown)} test(s) for "
                f"{function_name} not present in its 'tests' universe "
                f"(map may be stale): {sorted(unknown)[:3]}",
                file=sys.stderr,
            )
        if not config.test_runner_template:
            return ExecutionPlan(
                PlanKind.RUN_FULL, judgment=JUDGMENT_FULL_NO_TEMPLATE
            )

    return plan


def execute_plan(
    config: PseudoScopeConfig,
    mutations: list[MutatedSource],
    plan: ExecutionPlan,
) -> list[MutationRunResult]:
    """Run ``mutations`` according to ``plan`` (skip, selected subset, or full)."""
    if plan.kind is PlanKind.SKIP_AS_SURVIVED:
        return survived_without_running(mutations)
    if plan.kind is PlanKind.RUN_SELECTED:
        return run_mutation_tests(config, mutations, execution_plan=plan)
    return run_mutation_tests(config, mutations)


def analyze_function(
    config: PseudoScopeConfig,
    source: SourceFile,
    function_name: str,
    *,
    run_mutations: bool,
    coverage_map: CoverageMap | None = None,
) -> FunctionAnalysisOutcome:
    """
    Locate ``function_name``, generate mutations, and optionally run mutation tests.

    When ``run_mutations`` is False (baseline failed), returns a skipped outcome
    with ``reason`` ``baseline_failed``.

    When ``coverage_map`` is provided, the function's mutants are tested via the
    coverage-driven plan (selected subset, full fallback, or skip-as-survived);
    the chosen provenance is recorded on the outcome. With no map, behavior is
    identical to before (full ``--test-command`` per mutant).

    On restore failure, sets ``critical_error`` and re-raises via the returned
    outcome's caller (sweep aborts).
    """
    if not run_mutations:
        return FunctionAnalysisOutcome(
            function_name=function_name,
            status="skipped",
            reason="baseline_failed",
            start_line=None,
            end_line=None,
            location=None,
            mutation_results=[],
            classification_override="baseline_failed",
        )

    try:
        location = locate_function_body(source, function_name)
    except FunctionLocateError:
        return FunctionAnalysisOutcome(
            function_name=function_name,
            status="skipped",
            reason="locate_failed",
            start_line=None,
            end_line=None,
            location=None,
            mutation_results=[],
            classification_override=None,
        )

    try:
        mutations = generate_default_return_mutations(source, location)
    except UnsupportedReturnTypeError as exc:
        # Reference / unresolved-auto returns have no safe default; skip and label.
        return FunctionAnalysisOutcome(
            function_name=function_name,
            status="skipped",
            reason=f"unsupported_return_type:{exc.category}",
            start_line=location.start_line,
            end_line=location.end_line,
            location=location,
            mutation_results=[],
            classification_override="excluded_unsupported_return",
        )
    except MutationError:
        return FunctionAnalysisOutcome(
            function_name=function_name,
            status="skipped",
            reason="no_mutations",
            start_line=location.start_line,
            end_line=location.end_line,
            location=location,
            mutation_results=[],
            classification_override=None,
        )

    if not mutations:
        return FunctionAnalysisOutcome(
            function_name=function_name,
            status="skipped",
            reason="no_mutations",
            start_line=location.start_line,
            end_line=location.end_line,
            location=location,
            mutation_results=[],
            classification_override=None,
        )

    plan = resolve_execution_plan(config, coverage_map, function_name)
    selected_tests = (
        plan.nodeids if plan.kind is PlanKind.RUN_SELECTED else None
    )

    try:
        mutation_results = execute_plan(config, mutations, plan)
    except MutationExecutionError as exc:
        message = str(exc)
        if "CRITICAL:" in message:
            return FunctionAnalysisOutcome(
                function_name=function_name,
                status="skipped",
                reason="test_error",
                start_line=location.start_line,
                end_line=location.end_line,
                location=location,
                mutation_results=[],
                classification_override=None,
                critical_error=message,
                judgment=plan.judgment,
                selected_tests=selected_tests,
            )
        return FunctionAnalysisOutcome(
            function_name=function_name,
            status="skipped",
            reason="test_error",
            start_line=location.start_line,
            end_line=location.end_line,
            location=location,
            mutation_results=[],
            classification_override=None,
            judgment=plan.judgment,
            selected_tests=selected_tests,
        )

    if mutation_results and all(
        result.status == STATUS_UNCOMPILABLE for result in mutation_results
    ):
        # Every variant failed to compile: no scorable mutant. Skip + label so the
        # function is excluded from the analyzed / pass-rate denominators (rule 6),
        # mirroring the reference / unresolved-auto exclusion above.
        return FunctionAnalysisOutcome(
            function_name=function_name,
            status="skipped",
            reason="all_mutants_uncompilable",
            start_line=location.start_line,
            end_line=location.end_line,
            location=location,
            mutation_results=[],
            classification_override="excluded_uncompilable",
            judgment=plan.judgment,
            selected_tests=selected_tests,
        )

    return FunctionAnalysisOutcome(
        function_name=function_name,
        status="analyzed",
        reason=None,
        start_line=location.start_line,
        end_line=location.end_line,
        location=location,
        mutation_results=mutation_results,
        classification_override=None,
        judgment=plan.judgment,
        selected_tests=selected_tests,
    )

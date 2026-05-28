"""
Analyze a single function (locate → mutate → optional mutation tests).

Shared by single-function CLI mode and file sweep mode.
"""

from __future__ import annotations

from dataclasses import dataclass

from pseudoscope.executor import (
    MutationExecutionError,
    MutationRunResult,
    run_mutation_tests,
)
from pseudoscope.locate import FunctionBodyLocation, FunctionLocateError, locate_function_body
from pseudoscope.models import PseudoScopeConfig
from pseudoscope.mutate import MutationError, MutatedSource, generate_default_return_mutations
from pseudoscope.runner import TestRunResult
from pseudoscope.source import SourceFile


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


def analyze_function(
    config: PseudoScopeConfig,
    source: SourceFile,
    function_name: str,
    *,
    run_mutations: bool,
) -> FunctionAnalysisOutcome:
    """
    Locate ``function_name``, generate mutations, and optionally run mutation tests.

    When ``run_mutations`` is False (baseline failed), returns a skipped outcome
    with ``reason`` ``baseline_failed``.

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

    try:
        mutation_results = run_mutation_tests(config, mutations)
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
    )

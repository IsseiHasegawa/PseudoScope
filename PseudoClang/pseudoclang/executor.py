"""
Execute mutation tests: write, run tests, restore (Step 7).

Each mutation is applied to disk, tested, and reverted in a ``finally`` block
so the source file is never left mutated. Does not write JSON or classify
pseudo-tested functions.
"""

from __future__ import annotations

from dataclasses import dataclass

from pseudoclang.coverage_map import ExecutionPlan, PlanKind
from pseudoclang.models import PseudoScopeConfig
from pseudoclang.mutate import MutatedSource
from pseudoclang.runner import (
    TestRunError,
    TestRunResult,
    run_selected_test_command,
    run_test_command,
)
from pseudoclang.workspace import (
    WorkspaceError,
    restore_original_source,
    write_mutated_source,
)


class MutationExecutionError(Exception):
    """Raised when a mutation test cannot be written, run, or restored."""


@dataclass(frozen=True)
class MutationRunResult:
    """Outcome of one mutation write → test → restore cycle."""

    function_name: str
    mutation_type: str
    return_type_category: str
    replacement_body: str
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    runtime_seconds: float
    status: str
    restored: bool


def _status_from_test_result(test_result: TestRunResult) -> str:
    if test_result.timed_out:
        return "timeout"
    if test_result.exit_code == 0:
        return "survived"
    return "killed"


def _mutation_run_result(
    mutation: MutatedSource,
    test_result: TestRunResult,
    *,
    restored: bool,
) -> MutationRunResult:
    return MutationRunResult(
        function_name=mutation.function_name,
        mutation_type=mutation.mutation_type,
        return_type_category=mutation.return_type_category,
        replacement_body=mutation.replacement_body,
        exit_code=test_result.exit_code,
        timed_out=test_result.timed_out,
        stdout=test_result.stdout,
        stderr=test_result.stderr,
        runtime_seconds=test_result.runtime_seconds,
        status=_status_from_test_result(test_result),
        restored=restored,
    )


def run_selected_tests(
    config: PseudoScopeConfig,
    nodeids: tuple[str, ...] | list[str],
) -> bool:
    """
    Run only ``nodeids`` and return pass/fail (the selected-test runner).

    ``True`` = every selected test passed (mutant survived for this subset);
    ``False`` = at least one failed or the run timed out (mutant killed). Same
    subprocess/exit-code/timeout/cwd handling as the full command.
    """
    result = run_selected_test_command(config, nodeids)
    return not result.timed_out and result.exit_code == 0


def survived_without_running(
    mutations: list[MutatedSource],
) -> list[MutationRunResult]:
    """
    Synthesize ``survived`` results without touching disk or running tests.

    Used for the ``SKIP_AS_SURVIVED`` plan: when the map (trusted via
    ``--assume-coverage-complete``) says no test exercises the function, every
    mutant trivially survives. The source is never mutated, so ``restored`` is
    True.
    """
    return [
        MutationRunResult(
            function_name=mutation.function_name,
            mutation_type=mutation.mutation_type,
            return_type_category=mutation.return_type_category,
            replacement_body=mutation.replacement_body,
            exit_code=None,
            timed_out=False,
            stdout="",
            stderr="",
            runtime_seconds=0.0,
            status="survived",
            restored=True,
        )
        for mutation in mutations
    ]


def run_single_mutation_test(
    config: PseudoScopeConfig,
    mutation: MutatedSource,
    *,
    encoding: str = "utf-8",
    execution_plan: ExecutionPlan | None = None,
) -> MutationRunResult:
    """
    Write ``mutation``, run tests, and always restore the original source.

    When ``execution_plan`` selects a subset (``RUN_SELECTED``), only those
    nodeids run; otherwise the full ``config.test_command`` runs. Raises
    :class:`MutationExecutionError` on write, test start, or restore failure.
    Restore failures use a message marked as critical.
    """
    written = False
    restored = False
    test_result: TestRunResult | None = None
    run_selected = (
        execution_plan is not None and execution_plan.kind is PlanKind.RUN_SELECTED
    )

    try:
        try:
            write_mutated_source(mutation, encoding=encoding)
            written = True
        except WorkspaceError as exc:
            raise MutationExecutionError(
                f"Failed to write mutated source for {mutation.function_name} "
                f"to {mutation.path}: {exc}"
            ) from exc

        try:
            if run_selected:
                test_result = run_selected_test_command(
                    config, execution_plan.nodeids
                )
            else:
                test_result = run_test_command(config)
        except TestRunError as exc:
            raise MutationExecutionError(
                f"Failed to run test command for mutation on "
                f"{mutation.function_name}: {exc}"
            ) from exc
    finally:
        if written:
            try:
                restore_original_source(mutation, encoding=encoding)
                restored = True
            except WorkspaceError as exc:
                raise MutationExecutionError(
                    f"CRITICAL: Failed to restore original source at "
                    f"{mutation.path} after testing {mutation.function_name}. "
                    f"The file may still be mutated on disk: {exc}"
                ) from exc

    if test_result is None:
        raise MutationExecutionError(
            f"Mutation test for {mutation.function_name} did not produce a result."
        )

    return _mutation_run_result(mutation, test_result, restored=restored)


def run_mutation_tests(
    config: PseudoScopeConfig,
    mutations: list[MutatedSource],
    *,
    encoding: str = "utf-8",
    execution_plan: ExecutionPlan | None = None,
) -> list[MutationRunResult]:
    """Run each mutation sequentially; return all results.

    ``execution_plan`` (when ``RUN_SELECTED``) restricts every mutation's test
    run to the planned nodeids; otherwise the full command runs.
    """
    results: list[MutationRunResult] = []
    for mutation in mutations:
        results.append(
            run_single_mutation_test(
                config,
                mutation,
                encoding=encoding,
                execution_plan=execution_plan,
            )
        )
    return results

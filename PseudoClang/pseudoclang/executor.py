"""
Execute mutation tests: write, run tests, restore (Step 7).

Each mutation is applied to disk, tested, and reverted in a ``finally`` block
so the source file is never left mutated. A SIGTERM handler and an ``atexit``
hook back-stop that restore if the process is terminated mid-test (SIGKILL and
power loss remain unrecoverable). Does not write JSON or classify pseudo-tested
functions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pseudoclang import backup
from pseudoclang.coverage_map import ExecutionPlan, PlanKind
from pseudoclang.models import PseudoScopeConfig
from pseudoclang.mutate import MutatedSource
# Re-export _PENDING_RESTORES / _restore_pending_sources so existing importers
# (and tests) keep working now that the backstop lives in its own module.
from pseudoclang.restore_backstop import (  # noqa: F401
    _PENDING_RESTORES,
    _restore_pending_sources,
    install_backstop,
    register,
    unregister,
)
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
    # The exact shell command run for this mutant (empty when no test ran, e.g.
    # a SKIP_AS_SURVIVED synthesis). Surfaced only at trace verbosity.
    test_command: str = ""
    # Confirmation provenance for a selected-subset survivor re-judged against the
    # full --test-command (see run_single_mutation_test / combine_confirmation).
    # ``selected_status`` is the verdict from the selected subset (only set when a
    # confirmation run followed, i.e. the subset survived); ``confirmation`` is the
    # outcome label ("confirmed_survived" / "corrected_killed" / ...). Both None when
    # no confirmation ran (killed subset, confirmation disabled, or a full run).
    selected_status: str | None = None
    confirmation: str | None = None


#: Per-mutant status for a mutant that failed to compile (a.k.a. SKIPPED). It is
#: excluded from the mutation-score denominator instead of being miscounted as
#: ``killed``. Mirrors the coverage-map provenance convention (a distinct,
#: greppable label rather than overloading an existing state).
STATUS_UNCOMPILABLE = "uncompilable"

#: Compiler-diagnostic signatures that distinguish a build failure (our mutant did
#: not compile) from a genuine test failure (the mutant was killed).
_COMPILE_ERROR_PATTERNS = (
    re.compile(r"^.*?:\d+:\d+:\s*(?:fatal\s+)?error:", re.MULTILINE),  # clang/gcc
    re.compile(r"^.*?:\d+:\s*(?:fatal\s+)?error:", re.MULTILINE),      # gcc, no column
    re.compile(r"\berror C\d{4}\b"),                                   # MSVC
    re.compile(
        r"command '[^']*(?:clang|gcc|cc|c\+\+|g\+\+|cl)[^']*' "
        r"failed with exit (?:status|code)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:clang|gcc|cc1plus|cc1|c\+\+|g\+\+):\s*error:", re.IGNORECASE),
)


def _looks_like_compile_failure(
    test_result: TestRunResult,
    source_name: str | None = None,
) -> bool:
    """Heuristic: did the build fail (e.g. ``{}`` on a non-constructible type)?

    Scans combined stdout/stderr for C/C++ compiler-error diagnostics. When
    ``source_name`` is given, the diagnostic must also name the file we mutated: a
    genuine build failure from our mutation cites that file, whereas unrelated
    ``error:``-shaped test output almost never names that exact source. Conservative
    by design: an unmatched non-zero exit stays ``killed``, so a real test failure is
    never relabeled uncompilable and the mutation score is never inflated.

    Assumes the configured test command fails (non-zero exit) when the build fails;
    the exit-code gate in :func:`_status_from_test_result` runs first, so a command
    that tests a stale binary after a failed rebuild is out of scope.
    """
    blob = f"{test_result.stdout}\n{test_result.stderr}"
    if not any(pattern.search(blob) for pattern in _COMPILE_ERROR_PATTERNS):
        return False
    return source_name is None or source_name in blob


def _status_from_test_result(
    test_result: TestRunResult,
    *,
    source_name: str | None = None,
) -> str:
    if test_result.timed_out:
        return "timeout"
    if test_result.exit_code == 0:
        return "survived"
    if _looks_like_compile_failure(test_result, source_name):
        return STATUS_UNCOMPILABLE
    return "killed"


#: Confirmation outcome labels, keyed by the full-suite verdict of a mutant that
#: survived its selected subset. ``killed`` here means the map's subset missed a
#: covering test that the full suite caught.
_CONFIRMATION_LABELS = {
    "survived": "confirmed_survived",
    "killed": "corrected_killed",
    "timeout": "inconclusive_timeout",
}


def combine_confirmation(
    selected_status: str, full_status: str | None
) -> tuple[str, str | None]:
    """Combine a selected-subset verdict with an optional full-suite confirmation.

    The confirmation run only ever happens when ``selected_status == "survived"``
    (a subset survivor must be re-judged against the full ``--test-command`` before
    it is trusted as pseudo-tested). Returns ``(final_status, confirmation_label)``.
    When ``full_status`` is ``None`` no confirmation ran and the selected verdict
    stands unlabeled. Pure and side-effect free for unit testing.
    """
    if full_status is None:
        return selected_status, None
    return full_status, _CONFIRMATION_LABELS.get(
        full_status, f"confirmation_{full_status}"
    )


def _mutation_run_result(
    mutation: MutatedSource,
    test_result: TestRunResult,
    *,
    restored: bool,
    selected_status: str | None = None,
    confirmation: str | None = None,
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
        status=_status_from_test_result(test_result, source_name=mutation.path.name),
        restored=restored,
        test_command=test_result.test_command,
        selected_status=selected_status,
        confirmation=confirmation,
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

    On the selected path, a mutant that survives its subset is re-judged against
    the full ``config.test_command`` (while still on disk) when
    ``config.confirm_survivors`` is set: the map's subset may have missed a
    covering test, so a lone-subset survival is not trusted until the full suite
    confirms it. A full-suite kill corrects the verdict to ``killed``.
    """
    install_backstop()
    written = False
    restored = False
    test_result: TestRunResult | None = None
    selected_status: str | None = None
    confirmation: str | None = None
    run_selected = (
        execution_plan is not None and execution_plan.kind is PlanKind.RUN_SELECTED
    )

    try:
        try:
            write_mutated_source(mutation, encoding=encoding)
            written = True
            register(mutation.path, mutation.original_content)
            # Persist the original to disk too, so a hard crash (SIGKILL / power
            # loss) can still be undone later via `pseudoclang restore`.
            backup.record(
                mutation.path,
                original_bytes=mutation.original_content.encode(encoding),
                mutated_bytes=mutation.mutated_content.encode(encoding),
                function=mutation.function_name,
            )
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
                # A subset survivor is not trusted until the full suite confirms
                # it: the map may have missed a covering test. Re-judge the same
                # on-disk mutant against the full --test-command; a full kill
                # corrects the verdict (see combine_confirmation).
                if config.confirm_survivors:
                    subset_status = _status_from_test_result(
                        test_result, source_name=mutation.path.name
                    )
                    if subset_status == "survived":
                        full_result = run_test_command(config)
                        full_status = _status_from_test_result(
                            full_result, source_name=mutation.path.name
                        )
                        selected_status = subset_status
                        _, confirmation = combine_confirmation(
                            subset_status, full_status
                        )
                        # The confirming run is now authoritative for the verdict
                        # and the debug trail.
                        test_result = full_result
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
                # Unregister only after a successful restore. On failure (or a
                # KeyboardInterrupt mid-write) the path stays registered so the
                # atexit / SIGTERM backstop can retry the restore at exit.
                unregister(mutation.path)
                # The source is back to original, so drop its persistent backup;
                # a clean run therefore leaves nothing under output/backups.
                backup.clear(mutation.path)
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

    return _mutation_run_result(
        mutation,
        test_result,
        restored=restored,
        selected_status=selected_status,
        confirmation=confirmation,
    )


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

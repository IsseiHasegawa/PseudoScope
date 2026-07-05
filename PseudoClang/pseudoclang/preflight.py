"""
Preflight guards: does the test command actually rebuild the target, and is a
reused coverage map still fresh?

Two ways a run can silently report every function as ``pseudo_tested_candidate``:

1. The command that judges a mutant (``--test-command`` for a full run, or
   ``--test-runner-template`` for a selected subset) does not rebuild the
   extension. Each mutant is then tested against a stale binary, nothing is ever
   killed, and every function is misreported as pseudo-tested. We detect this
   deterministically: inject a compile error into the target, run the command
   once, and require it to fail. A command that rebuilds fails to compile; a
   command that skips the build runs the stale binary and the tests still pass,
   which is the footgun we refuse to run into.

2. A reused coverage map predates tests added to the suite. Those tests are
   outside every selected subset, so a mutant they would kill still "survives".
   The tool is runner-agnostic and cannot enumerate the live suite by itself, so
   this is an advisory by default and a best-effort check when ``--test-list-cmd``
   provides the current nodeids (it can only catch newly-ADDED tests, not an
   existing test edited to newly cover the function; the real remedy is to
   rebuild the map).
"""

from __future__ import annotations

import sys
from typing import Callable

from pseudoclang.coverage_map import CoverageMap
from pseudoclang.models import PseudoScopeConfig
from pseudoclang.restore_backstop import guarded_source_write
from pseudoclang.runner import (
    TestRunError,
    TestRunResult,
    run_selected_test_command,
    run_test_command,
    run_test_list_command,
)

#: Prepended to the target source. ``#error`` is a preprocessor directive that
#: fails compilation of any C/C++ translation unit, so a command that rebuilds
#: the mutated file must return non-zero.
_CANARY = "#error pstrace_template_rebuild_check\n"


class PreflightError(Exception):
    """Raised when a preflight safety check fails; the run must not proceed."""


def _probe_rebuild(
    config: PseudoScopeConfig,
    *,
    run_probe: Callable[[], TestRunResult],
    command_desc: str,
    build_hint: str,
) -> None:
    """
    Inject the compile-error canary, run ``run_probe``, and require it to fail.

    A command that rebuilds the target fails to compile the canary (non-zero
    exit) and passes the check. A command that skips the build runs the stale
    binary, the tests still pass (exit 0), and we raise :class:`PreflightError`.
    The target source is always restored via ``guarded_source_write`` (normal
    completion, exception, Ctrl-C, and the shared SIGTERM / atexit backstop).
    No-op when there is no target file.
    """
    target = config.target_file
    if target is None:
        return

    # newline="" preserves the file's byte content (see source.read_source_file).
    with open(target, "r", encoding="utf-8", newline="") as handle:
        original = handle.read()
    try:
        with guarded_source_write(target, _CANARY + original, original):
            result = run_probe()
    except TestRunError as exc:
        raise PreflightError(
            f"Could not run {command_desc} for the rebuild check: {exc}"
        ) from exc
    except OSError as exc:
        # The canary restore write failed; the file may still hold the probe. The
        # path stays registered, so the atexit / SIGTERM backstop retries at exit.
        raise PreflightError(
            f"Failed to restore {config.relative_file_path} after the rebuild "
            f"check (the backstop will retry at exit): {exc}"
        ) from exc

    command_passed = (not result.timed_out) and result.exit_code == 0
    if command_passed:
        raise PreflightError(
            f"{command_desc} did not rebuild after {config.relative_file_path} "
            "changed: a deliberate compile error still let the tests pass, so "
            "mutants would be judged against a stale binary and every function "
            f"would look pseudo-tested. {build_hint}, or pass --skip-runner-check "
            "to bypass this."
        )

    print(
        f"Preflight: {command_desc} rebuilds on source change (rebuild check passed)."
    )


def check_test_runner_rebuilds(
    config: PseudoScopeConfig, coverage_map: CoverageMap
) -> None:
    """
    Raise :class:`PreflightError` if ``--test-runner-template`` judges mutants
    against a stale build.

    No-op when there is no target file or the map lists no runnable tests to
    select.
    """
    if config.target_file is None:
        return

    universe = sorted(coverage_map.universe())
    if not universe:
        print(
            "Preflight: coverage map lists no tests to probe "
            "--test-runner-template; skipping the rebuild check.",
            file=sys.stderr,
        )
        return
    selection = (universe[0],)
    _probe_rebuild(
        config,
        run_probe=lambda: run_selected_test_command(config, selection),
        command_desc="--test-runner-template",
        build_hint="Add a build step to --test-runner-template (a rebuild before the tests)",
    )


def check_test_command_rebuilds(config: PseudoScopeConfig) -> None:
    """
    Raise :class:`PreflightError` if the full ``--test-command`` judges mutants
    against a stale build.

    Guards the RUN_FULL judging path (a no-map run, a no-template run, and the
    ABSENT-default / STARTUP_ONLY fallback under a map), which the
    ``--test-runner-template`` check does not cover. No-op when there is no
    target file.
    """
    _probe_rebuild(
        config,
        run_probe=lambda: run_test_command(config),
        command_desc="--test-command",
        build_hint=(
            "Add a build step to --test-command (e.g. 'pip install -e . && pytest')"
        ),
    )


def check_map_covers_current_tests(
    config: PseudoScopeConfig, coverage_map: CoverageMap
) -> None:
    """
    Warn (never raise) when a reused coverage map may predate current tests.

    The caller only invokes this when the map actually drives a SELECTED subset
    for some function. Always emits a one-line advisory that selected verdicts
    reflect the captured suite. When ``--test-list-cmd`` is set, additionally
    lists the current nodeids and warns about any that the map never recorded
    (newly-added tests that will not run against any mutant). Best-effort: a
    lister that cannot run is reported and skipped, not fatal.
    """
    created = coverage_map.created_at
    when = f" (captured {created})" if created else ""
    print(
        f"Note: selected-test verdicts reflect the test suite recorded in the "
        f"coverage map{when}; tests ADDED or CHANGED since are not re-selected "
        "and can cause false pseudo-tested verdicts. Rebuild the map after "
        "changing tests (coverage-map / --refresh-coverage-map).",
        file=sys.stderr,
    )

    if not config.test_list_cmd:
        return

    try:
        result = run_test_list_command(config)
    except TestRunError as exc:
        print(
            f"Warning: --test-list-cmd could not run ({exc}); skipping the "
            "new-test freshness check.",
            file=sys.stderr,
        )
        return
    if result.timed_out or result.exit_code != 0:
        print(
            "Warning: --test-list-cmd failed or timed out; skipping the new-test "
            "freshness check (its output was not a clean test list).",
            file=sys.stderr,
        )
        return

    live = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    # Only tests present now but not in the map matter: a removed test cannot
    # kill a mutant, so map-minus-live is ignored.
    new_tests = sorted(live - coverage_map.universe())
    if new_tests:
        sample = ", ".join(new_tests[:3])
        print(
            f"Warning: {len(new_tests)} test(s) in the current suite are absent "
            f"from the coverage map (e.g. {sample}). They will NOT run against "
            "any mutant, so a function they cover can be falsely reported "
            "pseudo-tested. Rebuild the map to include them "
            "(coverage-map / --refresh-coverage-map). Note: this cannot detect an "
            "existing test edited to newly cover a function.",
            file=sys.stderr,
        )
    else:
        print(
            f"Preflight: coverage map accounts for all {len(live)} current "
            "test(s) (no newly-added tests detected)."
        )

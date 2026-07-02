"""
Preflight guard: does ``--test-runner-template`` rebuild the target?

Selected-test mode judges every mutant with ``--test-runner-template``. If that
template runs tests without rebuilding the extension, each mutant is tested
against a stale binary, nothing is ever killed, and every function is
misreported as ``pseudo_tested_candidate``. The full ``--test-command`` baseline
does not catch this, because it exercises a different command.

Detect it deterministically before any analysis: inject a compile error into the
target file, run the template once, and require the run to fail. A template that
rebuilds fails to compile; a template that skips the build runs the stale binary
and the selected tests still pass, which is the bug we refuse to run into.
"""

from __future__ import annotations

import sys

from pseudoclang.coverage_map import CoverageMap
from pseudoclang.models import PseudoScopeConfig
from pseudoclang.runner import TestRunError, run_selected_test_command

#: Prepended to the target source. ``#error`` is a preprocessor directive that
#: fails compilation of any C/C++ translation unit, so a template that rebuilds
#: the mutated file must return non-zero.
_CANARY = "#error pstrace_template_rebuild_check\n"


class PreflightError(Exception):
    """Raised when a preflight safety check fails; the run must not proceed."""


def check_test_runner_rebuilds(
    config: PseudoScopeConfig, coverage_map: CoverageMap
) -> None:
    """
    Raise :class:`PreflightError` if the template judges against a stale build.

    No-op when there is no target file or the map lists no runnable tests to
    select. The target source is always restored.
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

    target = config.target_file
    original = target.read_text(encoding="utf-8")
    try:
        # finally-restore covers normal completion, failure, and Ctrl-C; the
        # window (one build + test) is short.
        target.write_text(_CANARY + original, encoding="utf-8")
        try:
            result = run_selected_test_command(config, selection)
        except TestRunError as exc:
            raise PreflightError(
                f"Could not run --test-runner-template for the rebuild check: {exc}"
            ) from exc
    finally:
        target.write_text(original, encoding="utf-8")

    template_passed = (not result.timed_out) and result.exit_code == 0
    if template_passed:
        raise PreflightError(
            f"--test-runner-template did not rebuild after {config.relative_file_path} "
            "changed: a deliberate compile error still let the selected tests pass. "
            "Selected mutants would be judged against a stale binary, so every function "
            "would look pseudo-tested. Add a build step to --test-runner-template "
            "(a rebuild before pytest), or pass --skip-runner-check to bypass this."
        )

    print(
        "Preflight: --test-runner-template rebuilds on source change "
        "(rebuild check passed)."
    )

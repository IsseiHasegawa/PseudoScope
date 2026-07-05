"""
Run the configured test command (Step 6).

Executes tests from the project root and captures outcome. Does not mutate
source files, classify pseudo-tested functions, or write JSON.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass

from pseudoclang.models import PseudoScopeConfig

SELECTION_PLACEHOLDER = "{selection}"


class TestRunError(Exception):
    """Raised when the test command cannot be started."""


@dataclass(frozen=True)
class TestRunResult:
    """Outcome of one test command execution."""

    test_command: str
    project_root: str
    exit_code: int | None
    stdout: str
    stderr: str
    runtime_seconds: float
    timed_out: bool


def _decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _run_shell_command(command: str, config: PseudoScopeConfig) -> TestRunResult:
    """
    Run ``command`` from ``config.project_root`` with the shared handling.

    Identical subprocess/exit-code/timeout/cwd behavior for both the full test
    command and a selected-subset command.
    """
    start = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=config.project_root,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        runtime_seconds = time.perf_counter() - start
        return TestRunResult(
            test_command=command,
            project_root=str(config.project_root),
            exit_code=None,
            stdout=_decode_output(exc.stdout),
            stderr=_decode_output(exc.stderr),
            runtime_seconds=runtime_seconds,
            timed_out=True,
        )
    except OSError as exc:
        raise TestRunError(
            f"Failed to start test command {command!r} "
            f"in {config.project_root}: {exc}"
        ) from exc

    runtime_seconds = time.perf_counter() - start
    return TestRunResult(
        test_command=command,
        project_root=str(config.project_root),
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        runtime_seconds=runtime_seconds,
        timed_out=False,
    )


def run_test_command(config: PseudoScopeConfig) -> TestRunResult:
    """
    Run ``config.test_command`` from ``config.project_root``.

    Returns a :class:`TestRunResult` on completion or timeout. Raises
    :class:`TestRunError` if the process cannot be started.
    """
    return _run_shell_command(config.test_command, config)


def run_test_list_command(config: PseudoScopeConfig) -> TestRunResult:
    """
    Run ``config.test_list_cmd`` to enumerate the current test nodeids.

    Uses the same subprocess/cwd/timeout/capture handling as every other
    command; the caller parses ``stdout`` (one nodeid per line). Raises
    :class:`TestRunError` if the process cannot be started, or if no
    ``--test-list-cmd`` is configured.
    """
    if not config.test_list_cmd:
        raise TestRunError(
            "Test list requested but no --test-list-cmd is set."
        )
    return _run_shell_command(config.test_list_cmd, config)


def build_selected_command(template: str, nodeids: tuple[str, ...] | list[str]) -> str:
    """
    Substitute shell-quoted ``nodeids`` into ``template``'s ``{selection}``.

    Each nodeid is quoted independently because parametrized pytest nodeids
    legitimately contain spaces, ``::``, brackets, commas, and quotes.
    """
    selection = " ".join(shlex.quote(nodeid) for nodeid in nodeids)
    return template.replace(SELECTION_PLACEHOLDER, selection)


def run_selected_test_command(
    config: PseudoScopeConfig,
    nodeids: tuple[str, ...] | list[str],
) -> TestRunResult:
    """
    Run only ``nodeids`` via ``config.test_runner_template``.

    The template must contain ``{selection}`` and is validated at config time.
    Raises :class:`TestRunError` if no template is configured.
    """
    if not config.test_runner_template:
        raise TestRunError(
            "Selected test run requested but no --test-runner-template is set."
        )
    command = build_selected_command(config.test_runner_template, nodeids)
    return _run_shell_command(command, config)

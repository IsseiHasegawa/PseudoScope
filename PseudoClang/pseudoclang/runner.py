"""
Run the configured test command (Step 6).

Executes tests from the project root and captures outcome. Does not mutate
source files, classify pseudo-tested functions, or write JSON.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from pseudoclang.models import PseudoScopeConfig


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


def run_test_command(config: PseudoScopeConfig) -> TestRunResult:
    """
    Run ``config.test_command`` from ``config.project_root``.

    Returns a :class:`TestRunResult` on completion or timeout. Raises
    :class:`TestRunError` if the process cannot be started.
    """
    start = time.perf_counter()
    try:
        completed = subprocess.run(
            config.test_command,
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
            test_command=config.test_command,
            project_root=str(config.project_root),
            exit_code=None,
            stdout=_decode_output(exc.stdout),
            stderr=_decode_output(exc.stderr),
            runtime_seconds=runtime_seconds,
            timed_out=True,
        )
    except OSError as exc:
        raise TestRunError(
            f"Failed to start test command {config.test_command!r} "
            f"in {config.project_root}: {exc}"
        ) from exc

    runtime_seconds = time.perf_counter() - start
    return TestRunResult(
        test_command=config.test_command,
        project_root=str(config.project_root),
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        runtime_seconds=runtime_seconds,
        timed_out=False,
    )

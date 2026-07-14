"""
Verbosity-gated console output.

A single integer ``config.verbosity`` decides how much a run narrates:

    0  quiet    errors only (plus the final result summary)
    1  normal   the default narration (progress, per-function summaries)
    2  verbose  + per-function plan/judgment and per-mutant exit/runtime
    3  trace     + each mutant's exact command and captured stdout/stderr tail

These helpers are the only place that reads ``config.verbosity`` for ordinary
output, so call sites stay readable (``detail(config, ...)`` instead of an
``if`` around every ``print``). Errors and warnings do not go through here: they
must always reach the user, so they keep printing to ``sys.stderr`` directly.
"""

from __future__ import annotations

#: How many trailing lines of a captured stream to show at trace level.
DEFAULT_TAIL_LINES = 20


def is_quiet(config) -> bool:
    return config.verbosity <= 0


def is_verbose(config) -> bool:
    return config.verbosity >= 2


def is_trace(config) -> bool:
    return config.verbosity >= 3


def chatty(config, message: str = "") -> None:
    """Normal-level narration; suppressed by ``-q`` (verbosity 0)."""
    if config.verbosity >= 1:
        print(message)


def detail(config, message: str = "") -> None:
    """Verbose-level (``-v``) diagnostic output."""
    if config.verbosity >= 2:
        print(message)


def trace(config, message: str = "") -> None:
    """Trace-level (``-vv``) output (raw commands, captured stream tails)."""
    if config.verbosity >= 3:
        print(message)


def tail_lines(text: str, limit: int = DEFAULT_TAIL_LINES) -> tuple[list[str], int]:
    """Return ``(last <=limit lines, total line count)`` for ``text``.

    Blank/whitespace-only text yields ``([], 0)`` so callers can skip it.
    """
    if not text.strip():
        return [], 0
    lines = text.splitlines()
    return lines[-limit:], len(lines)

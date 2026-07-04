"""
Best-effort source-restore backstop shared by mutation runs and the preflight.

A caller's own ``finally`` is the primary restore path; it covers normal
completion, failure, exceptions, and Ctrl-C (SIGINT). This adds the two cases a
``finally`` cannot cover while a source is edited on disk: ``SIGTERM`` (terminates
without unwinding) and an abnormal interpreter exit. ``SIGKILL`` and power loss
remain unrecoverable.
"""

from __future__ import annotations

import atexit
import signal
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pseudoclang import backup

# Sources currently edited on disk, mapped to their original content. The
# per-operation ``finally`` is the primary restore; this registry lets the
# ``atexit`` / ``SIGTERM`` backstop restore a source in the narrow window where
# that ``finally`` cannot run.
_PENDING_RESTORES: dict = {}
_BACKSTOP_INSTALLED = False


def _restore_pending_sources() -> None:
    """Best-effort restore of every source still edited on disk (signal / atexit)."""
    for path, original in list(_PENDING_RESTORES.items()):
        try:
            path.write_text(original, encoding="utf-8", newline="")
        except OSError:  # pragma: no cover - disk error during emergency restore
            continue  # keep it registered so a later retry can still restore
        _PENDING_RESTORES.pop(path, None)


def install_backstop() -> None:
    """Install an ``atexit`` + ``SIGTERM`` restore backstop once (best effort)."""
    global _BACKSTOP_INSTALLED
    if _BACKSTOP_INSTALLED:
        return
    atexit.register(_restore_pending_sources)
    try:
        def _on_sigterm(signum, frame):
            _restore_pending_sources()  # synchronous: source is safe even if the
            raise SystemExit(128 + signum)  # SystemExit below cannot propagate

        signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:  # pragma: no cover - not in the main thread
        # atexit still applies; the SIGTERM handler just cannot be installed here.
        pass
    _BACKSTOP_INSTALLED = True


def register(path: Path, original: str) -> None:
    """Record ``path``'s original content so the backstop can restore it."""
    _PENDING_RESTORES[path] = original


def unregister(path: Path) -> None:
    """Drop ``path`` from the registry once its own ``finally`` has restored it."""
    _PENDING_RESTORES.pop(path, None)


@contextmanager
def guarded_source_write(
    path: Path,
    new_content: str,
    original_content: str,
    *,
    encoding: str = "utf-8",
) -> Iterator[None]:
    """Write ``new_content`` to ``path``; always restore ``original_content``.

    The ``finally`` covers normal completion, exceptions, and Ctrl-C; the shared
    registry covers ``SIGTERM`` and abnormal exit.
    """
    install_backstop()
    path.write_text(new_content, encoding=encoding, newline="")
    register(path, original_content)
    # Persist the original so a hard crash can be undone via `pseudoclang restore`.
    backup.record(
        path,
        original_bytes=original_content.encode(encoding),
        mutated_bytes=new_content.encode(encoding),
    )
    try:
        yield
    finally:
        # Restore first, then unregister only on success: if the restore write
        # raises, leave the path registered so the atexit / SIGTERM backstop can
        # retry it at exit instead of silently disarming the last-resort recovery.
        path.write_text(original_content, encoding=encoding, newline="")
        unregister(path)
        backup.clear(path)

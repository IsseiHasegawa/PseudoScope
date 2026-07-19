"""
Workspace file operations for mutated source (Step 5).

Writes and restores target source files on disk. Does not run tests or write JSON.

Always pair :func:`write_mutated_source` with :func:`restore_original_source`
so the workspace is not left mutated after analysis, for example::

    write_mutated_source(mutation)
    try:
        result = run_test_command(config)
    finally:
        restore_original_source(mutation)
"""

from __future__ import annotations

from pseudoclang.atomicio import atomic_write_text
from pseudoclang.mutate import MutatedSource


class WorkspaceError(Exception):
    """Raised when writing or restoring a source file on disk fails."""


def write_mutated_source(
    mutation: MutatedSource,
    *,
    encoding: str = "utf-8",
) -> None:
    """
    Write ``mutation.mutated_content`` to ``mutation.path``.

    Pair with :func:`restore_original_source` in a ``finally`` block so the
    file is not left mutated on disk after the program exits.
    """
    try:
        # Atomic (temp + os.replace) so a failed write never truncates the target;
        # bytes are written as-is (no LF -> os.linesep translation), keeping a CRLF
        # file's endings intact through the mutate/restore cycle.
        atomic_write_text(mutation.path, mutation.mutated_content, encoding=encoding)
    except OSError as exc:
        raise WorkspaceError(
            f"Failed to write mutated source to {mutation.path}: {exc}"
        ) from exc


def restore_original_source(
    mutation: MutatedSource,
    *,
    encoding: str = "utf-8",
) -> None:
    """
    Write ``mutation.original_content`` back to ``mutation.path``.

    Use after :func:`write_mutated_source`, typically in a ``finally`` block.
    """
    try:
        # Atomic so a failed restore cannot corrupt the source it is protecting.
        atomic_write_text(mutation.path, mutation.original_content, encoding=encoding)
    except OSError as exc:
        raise WorkspaceError(
            f"Failed to restore original source at {mutation.path}: {exc}"
        ) from exc

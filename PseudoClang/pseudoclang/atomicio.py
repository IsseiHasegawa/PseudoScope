"""
Atomic file writes: a write either fully lands or leaves the original untouched.

The target's source files are never allowed to end up truncated or partially
written. ``Path.write_text`` / ``write_bytes`` truncate the file first and then
write, so a failure partway (disk full, I/O error, quota) destroys the original
with no recovery. Every write that touches a target source file (mutation,
restore, the preflight canary, crash recovery, snapshot rollback) and every
recovery-data copy goes through here instead.

The strategy mirrors :func:`pseudoclang.backup._save_manifest`: write the bytes to
a temporary file in the same directory, then :func:`os.replace` it onto the
target. ``os.replace`` is atomic within a filesystem, so a mid-write failure only
ever damages the temp file (which is cleaned up); the real file is never touched.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file + :func:`os.replace`).

    Writes through a symlink to its real file (so a symlinked target stays a
    symlink), preserves the existing file's permission bits, and cleans up the
    temp file on failure. Raises :class:`OSError` if the write cannot complete;
    the target is left exactly as it was in that case.
    """
    # Resolve symlinks so we replace the real file, not turn the link into a
    # regular file (matches the write-through-symlink behavior of write_text).
    target = os.path.realpath(str(path))
    directory = os.path.dirname(target) or "."

    # Preserve the target's permission bits: os.replace swaps in the temp file's
    # inode, so without this the mode would reset to the mkstemp default.
    try:
        mode: int | None = os.stat(target).st_mode
    except OSError:
        mode = None  # target does not exist yet (e.g. restoring a deleted file)

    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".pstrace-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        if mode is not None:
            os.chmod(tmp_name, mode & 0o7777)
        os.replace(tmp_name, target)
    except OSError:
        # The real file was never touched; drop the temp and let the caller wrap
        # the error (e.g. into WorkspaceError) rather than losing data.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_text(
    path: Path | str, content: str, *, encoding: str = "utf-8"
) -> None:
    """Atomically write ``content`` to ``path`` as ``encoding`` bytes.

    Encodes and writes raw bytes (no newline translation), so CRLF / lone-CR /
    BOM / missing-final-newline round-trip byte-for-byte.
    """
    atomic_write_bytes(path, content.encode(encoding))

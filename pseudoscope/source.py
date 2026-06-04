"""
Read target source files (Step 2).

Reads file contents into memory only. Does not modify files on disk, locate
functions, mutate code, run tests, or write JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pseudoscope.models import PseudoScopeConfig


class SourceReadError(Exception):
    """Raised when the target source file cannot be read or decoded."""


@dataclass(frozen=True)
class SourceFile:
    """In-memory representation of a source file that was read from disk."""

    path: Path
    relative_path: Path
    content: str
    encoding: str
    line_count: int


def read_source_file(
    config: PseudoScopeConfig,
    *,
    encoding: str = "utf-8",
) -> SourceFile:
    """
    Read ``config.target_file`` without modifying it.

    Raises :class:`SourceReadError` if the file cannot be read or decoded.
    """
    if config.target_file is None or config.relative_file_path is None:
        raise SourceReadError(
            "Target file is not configured. Set --file before reading source."
        )
    path = config.target_file

    try:
        content = path.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        raise SourceReadError(
            f"Could not decode {path} as {encoding}: {exc}"
        ) from exc
    except OSError as exc:
        raise SourceReadError(f"Could not read source file {path}: {exc}") from exc

    line_count = len(content.splitlines())

    return SourceFile(
        path=path,
        relative_path=config.relative_file_path,
        content=content,
        encoding=encoding,
        line_count=line_count,
    )

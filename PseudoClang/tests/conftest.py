"""Shared fixtures for the pseudoclang test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from pseudoclang.source import SourceFile


def _make_source(content: str, name: str = "sample.c") -> SourceFile:
    """Build an in-memory SourceFile from a string (no disk I/O).

    The locate/discover/mutate code paths only read ``path.suffix`` and
    ``content``, so a synthetic SourceFile is enough to exercise them.
    """
    path = Path(name)
    return SourceFile(
        path=path,
        relative_path=path,
        content=content,
        encoding="utf-8",
        line_count=len(content.splitlines()),
    )


@pytest.fixture
def make_source():
    """Return a factory: ``make_source(content, name="sample.c") -> SourceFile``."""
    return _make_source

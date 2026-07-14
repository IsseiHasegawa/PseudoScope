"""Shared fixtures for the pseudoclang test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from pseudoclang.backup import BACKUPS_DIR_ENV
from pseudoclang.snapshots import SNAPSHOTS_DIR_ENV
from pseudoclang.source import SourceFile


@pytest.fixture(autouse=True)
def _isolate_backups(tmp_path_factory, monkeypatch):
    """Point the crash-safety backups and snapshot history at temp dirs.

    Mutation/preflight runs persist a backup of each source before mutating it,
    and a full run captures a recovery-point snapshot; without this every test
    would write into the real PseudoClang/output/backups and output/snapshots.
    """
    backups = tmp_path_factory.mktemp("pseudoclang-backups")
    monkeypatch.setenv(BACKUPS_DIR_ENV, str(backups))
    snaps = tmp_path_factory.mktemp("pseudoclang-snapshots")
    monkeypatch.setenv(SNAPSHOTS_DIR_ENV, str(snaps))


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

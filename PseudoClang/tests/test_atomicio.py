"""Atomic source writes: a failed write must never truncate the target (H13)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pseudoclang.atomicio import atomic_write_bytes, atomic_write_text
from pseudoclang.executor import MutationExecutionError, run_single_mutation_test
from pseudoclang.locate import locate_function_body
from pseudoclang.models import PseudoScopeConfig
from pseudoclang.mutate import generate_default_return_mutations
from pseudoclang.source import SourceFile
from pseudoclang.workspace import WorkspaceError, write_mutated_source

_C_SOURCE = "int add(int a, int b){ return a + b; }\n"


def _raise_oserror(*_args, **_kwargs):
    raise OSError("simulated disk full")


def _temp_leftovers(directory: Path) -> list[Path]:
    return [p for p in directory.iterdir() if p.name.startswith(".pstrace-")]


# -- atomicio helper --------------------------------------------------------


def test_writes_exact_bytes_and_preserves_mode(tmp_path):
    f = tmp_path / "x.c"
    f.write_bytes(b"orig\n")
    os.chmod(f, 0o600)

    atomic_write_bytes(f, b"new content\r\n")

    assert f.read_bytes() == b"new content\r\n"
    assert (f.stat().st_mode & 0o777) == 0o600  # mode preserved across os.replace


def test_failed_replace_leaves_original_and_cleans_temp(tmp_path, monkeypatch):
    f = tmp_path / "x.c"
    original = b"do not lose me\n"
    f.write_bytes(original)
    monkeypatch.setattr("pseudoclang.atomicio.os.replace", _raise_oserror)

    with pytest.raises(OSError):
        atomic_write_bytes(f, b"mutated")

    assert f.read_bytes() == original  # untouched, not truncated
    assert _temp_leftovers(tmp_path) == []  # temp file cleaned up


def test_writes_through_symlink(tmp_path):
    real = tmp_path / "real.c"
    real.write_bytes(b"orig\n")
    link = tmp_path / "link.c"
    link.symlink_to(real)

    atomic_write_text(link, "updated\n")

    assert link.is_symlink()  # still a symlink, not replaced by a regular file
    assert real.read_bytes() == b"updated\n"  # the real file received the content


def test_creates_missing_target(tmp_path):
    f = tmp_path / "new.c"
    atomic_write_bytes(f, b"created\n")
    assert f.read_bytes() == b"created\n"


def test_text_roundtrips_crlf_and_bom(tmp_path):
    f = tmp_path / "x.c"
    f.write_bytes(b"orig")
    content = "﻿line1\r\nline2\r\n"  # BOM + CRLF
    atomic_write_text(f, content)
    assert f.read_bytes() == content.encode("utf-8")  # no newline translation


# -- H13 regression: failed mutation write must not destroy the source ------


def _mutation(tmp_path: Path):
    f = tmp_path / "x.c"
    f.write_text(_C_SOURCE)
    src = SourceFile(
        path=f, relative_path=Path("x.c"), content=_C_SOURCE, encoding="utf-8", line_count=1
    )
    loc = locate_function_body(src, "add")
    return f, generate_default_return_mutations(src, loc)[0]


def _config(tmp_path: Path, target: Path) -> PseudoScopeConfig:
    return PseudoScopeConfig(
        project_root=tmp_path,
        relative_file_path=Path(target.name),
        target_file=target,
        function_name="add",
        test_command="true",
        output_path=tmp_path / "out.json",
        timeout_seconds=30,
        mode=None,
        lang=None,
    )


def test_write_mutated_source_failure_does_not_truncate(tmp_path, monkeypatch):
    f, mutation = _mutation(tmp_path)
    monkeypatch.setattr("pseudoclang.atomicio.os.replace", _raise_oserror)

    with pytest.raises(WorkspaceError):
        write_mutated_source(mutation)

    assert f.read_text() == _C_SOURCE  # original intact (would be truncated before)
    assert _temp_leftovers(tmp_path) == []


def test_run_single_mutation_write_failure_preserves_source(tmp_path, monkeypatch):
    f, mutation = _mutation(tmp_path)
    monkeypatch.setattr("pseudoclang.atomicio.os.replace", _raise_oserror)

    with pytest.raises(MutationExecutionError):
        run_single_mutation_test(_config(tmp_path, f), mutation)

    assert f.read_text() == _C_SOURCE  # source not destroyed by the failed write
    assert _temp_leftovers(tmp_path) == []


def test_clean_run_leaves_no_temp_files(tmp_path):
    f, mutation = _mutation(tmp_path)
    result = run_single_mutation_test(_config(tmp_path, f), mutation)
    assert result.restored is True
    assert f.read_text() == _C_SOURCE  # restored byte-for-byte
    assert _temp_leftovers(tmp_path) == []  # no atomic-write temp left behind

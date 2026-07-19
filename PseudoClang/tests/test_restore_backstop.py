"""Tests for the SIGTERM / atexit restore backstop (shared module + executor)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pseudoclang import executor
from pseudoclang.executor import (
    _PENDING_RESTORES,
    MutationExecutionError,
    _restore_pending_sources,
    run_single_mutation_test,
)
from pseudoclang.restore_backstop import guarded_source_write
from pseudoclang.workspace import WorkspaceError
from pseudoclang.locate import locate_function_body
from pseudoclang.models import PseudoScopeConfig
from pseudoclang.mutate import generate_default_return_mutations
from pseudoclang.source import SourceFile

_C_SOURCE = "int add(int a, int b){ return a + b; }\n"


def _config(tmp_path: Path, target: Path) -> PseudoScopeConfig:
    return PseudoScopeConfig(
        project_root=tmp_path,
        relative_file_path=Path(target.name),
        target_file=target,
        function_name="add",
        test_command="true",
        output_path=tmp_path / "out.json",
        timeout_seconds=60,
        mode=None,
        lang=None,
    )


def test_restore_pending_sources_restores_and_clears(tmp_path):
    f = tmp_path / "x.c"
    original = "ORIGINAL CONTENT\n"
    f.write_text(original)
    f.write_text("MUTATED CONTENT\n")  # simulate a mutation left on disk
    _PENDING_RESTORES[f] = original
    try:
        _restore_pending_sources()
        assert f.read_text() == original  # backstop restored it
        assert f not in _PENDING_RESTORES  # and cleared the registry
    finally:
        _PENDING_RESTORES.pop(f, None)


def test_run_single_mutation_restores_and_clears_registry(tmp_path):
    f = tmp_path / "x.c"
    f.write_text(_C_SOURCE)
    original = f.read_text()
    src = SourceFile(
        path=f, relative_path=Path("x.c"), content=original, encoding="utf-8", line_count=1
    )
    loc = locate_function_body(src, "add")
    mutation = generate_default_return_mutations(src, loc)[0]

    result = run_single_mutation_test(_config(tmp_path, f), mutation)

    assert result.restored is True
    assert f.read_text() == original  # restored byte-for-byte
    assert f not in _PENDING_RESTORES  # registry cleared after the per-mutant finally


def test_guarded_source_write_restores_on_success(tmp_path):
    f = tmp_path / "x.c"
    f.write_text("ORIG\n")
    with guarded_source_write(f, "NEW\n", "ORIG\n"):
        assert f.read_text() == "NEW\n"  # new content is on disk during the block
        assert f in _PENDING_RESTORES  # and registered with the backstop
    assert f.read_text() == "ORIG\n"  # restored on exit
    assert f not in _PENDING_RESTORES  # and unregistered


def test_guarded_source_write_restores_on_exception(tmp_path):
    f = tmp_path / "x.c"
    f.write_text("ORIG\n")
    with pytest.raises(RuntimeError):
        with guarded_source_write(f, "NEW\n", "ORIG\n"):
            assert f in _PENDING_RESTORES
            raise RuntimeError("boom")
    assert f.read_text() == "ORIG\n"  # restored despite the exception
    assert f not in _PENDING_RESTORES


def test_guarded_source_write_stays_registered_when_restore_write_fails(tmp_path, monkeypatch):
    # If the restore write itself fails, the path must remain registered so the
    # atexit/SIGTERM backstop can retry -- it must NOT be silently unregistered.
    from pseudoclang import restore_backstop

    f = tmp_path / "x.c"
    f.write_text("ORIG\n")
    real_write = restore_backstop.atomic_write_text

    def flaky_write(path, content, *, encoding="utf-8"):
        # Fail only the restore write (original content back to the source), so
        # the assertion is robust to any other bookkeeping writes in between.
        if content == "ORIG\n":
            raise OSError("disk full")
        return real_write(path, content, encoding=encoding)

    monkeypatch.setattr(restore_backstop, "atomic_write_text", flaky_write)
    with pytest.raises(OSError):
        with guarded_source_write(f, "NEW\n", "ORIG\n"):
            pass
    assert f in _PENDING_RESTORES  # backstop still armed
    _PENDING_RESTORES.pop(f, None)  # test cleanup


def test_run_single_mutation_stays_registered_when_restore_fails(tmp_path, monkeypatch):
    f = tmp_path / "x.c"
    f.write_text(_C_SOURCE)
    src = SourceFile(
        path=f, relative_path=Path("x.c"), content=_C_SOURCE, encoding="utf-8", line_count=1
    )
    loc = locate_function_body(src, "add")
    mutation = generate_default_return_mutations(src, loc)[0]

    def boom(_mutation, encoding="utf-8"):
        raise WorkspaceError("cannot restore")

    monkeypatch.setattr(executor, "restore_original_source", boom)
    with pytest.raises(MutationExecutionError):
        run_single_mutation_test(_config(tmp_path, f), mutation)

    assert f in _PENDING_RESTORES  # left armed for the atexit/SIGTERM backstop
    _PENDING_RESTORES.pop(f, None)  # test cleanup

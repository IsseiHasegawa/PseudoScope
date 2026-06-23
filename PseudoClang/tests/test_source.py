"""Tests for pseudoclang.source (reading target files)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pseudoclang.models import PseudoScopeConfig
from pseudoclang.source import SourceReadError, read_source_file


def _config(target_file, relative_file_path) -> PseudoScopeConfig:
    return PseudoScopeConfig(
        project_root=Path("/proj"),
        relative_file_path=relative_file_path,
        target_file=target_file,
        function_name="f",
        test_command="x",
        output_path=Path("out.json"),
        timeout_seconds=1,
        mode=None,
        lang=None,
    )


def test_reads_content_and_counts_lines(tmp_path):
    target = tmp_path / "a.c"
    target.write_text("int main(){\n  return 0;\n}\n", encoding="utf-8")

    source = read_source_file(_config(target, Path("a.c")))

    assert source.content == "int main(){\n  return 0;\n}\n"
    assert source.line_count == 3
    assert source.path == target
    assert source.relative_path == Path("a.c")
    assert source.encoding == "utf-8"


def test_missing_target_raises(tmp_path):
    with pytest.raises(SourceReadError):
        read_source_file(_config(None, None))


def test_nonexistent_file_raises(tmp_path):
    with pytest.raises(SourceReadError):
        read_source_file(_config(tmp_path / "nope.c", Path("nope.c")))


def test_undecodable_bytes_raise(tmp_path):
    target = tmp_path / "bad.c"
    target.write_bytes(b"\xff\xfe\x00 not utf-8 \xff")
    with pytest.raises(SourceReadError):
        read_source_file(_config(target, Path("bad.c")))

"""The target source is restored byte-for-byte, including CRLF / lone-CR endings."""

from __future__ import annotations

from pathlib import Path

import pytest

from pseudoclang.locate import locate_function_body
from pseudoclang.models import PseudoScopeConfig
from pseudoclang.mutate import generate_default_return_mutations
from pseudoclang.source import read_source_file
from pseudoclang.workspace import restore_original_source, write_mutated_source


def _config(tmp_path: Path, target: Path) -> PseudoScopeConfig:
    return PseudoScopeConfig(
        project_root=tmp_path,
        relative_file_path=Path(target.name),
        target_file=target,
        function_name="add",
        test_command="true",
        output_path=tmp_path / "o.json",
        timeout_seconds=10,
        mode=None,
        lang=None,
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"int add(int a, int b) {\r\n    return a + b;\r\n}\r\n",  # CRLF
        b"int add(int a, int b) {\n    return a + b;\n}\n",  # LF
        b"int add(int a, int b) {\n    return a + b;\n}",  # no final newline
        b"\xef\xbb\xbfint add(int a, int b) {\n    return a + b;\n}\n",  # UTF-8 BOM
        "// café\r\nint add(int a, int b) {\r\n    return a + b;\r\n}\r\n".encode(),  # CRLF + non-ASCII
    ],
)
def test_target_restored_byte_identical(tmp_path, raw):
    target = tmp_path / "x.c"
    target.write_bytes(raw)
    before = target.read_bytes()

    src = read_source_file(_config(tmp_path, target))
    loc = locate_function_body(src, "add")
    mutation = generate_default_return_mutations(src, loc)[0]

    write_mutated_source(mutation)  # file is mutated on disk here
    restore_original_source(mutation)  # PseudoClang's "always restore"

    assert target.read_bytes() == before  # byte-for-byte, endings preserved

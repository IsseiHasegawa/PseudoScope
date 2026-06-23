"""Tests for pseudoclang.treesitter_util (parsing helpers)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pseudoclang import treesitter_util as ts


@pytest.mark.parametrize(
    "requested,discovered,expected",
    [
        ("foo", "foo", True),
        ("foo", "bar", False),
        ("Ns::foo", "foo", True),  # qualified request matches bare name
        ("Ns::foo", "bar", False),
        ("foo", "Ns::foo", False),  # bare request does not match qualified
    ],
)
def test_function_names_match(requested, discovered, expected):
    assert ts.function_names_match(requested, discovered) is expected


def test_index_to_line():
    content = "a\nb\nc"
    assert ts.index_to_line(content, 0) == 1
    assert ts.index_to_line(content, 2) == 2  # 'b'
    assert ts.index_to_line(content, 4) == 3  # 'c'


def test_line_start_index():
    content = "ab\ncd\nef"
    assert ts.line_start_index(content, 0) == 0
    assert ts.line_start_index(content, 1) == 0
    assert ts.line_start_index(content, 4) == 3  # inside 'cd'
    assert ts.line_start_index(content, 7) == 6  # inside 'ef'


@pytest.mark.parametrize(
    "name,expected",
    [
        ("x.c", True),
        ("x.h", True),
        ("x.cpp", True),
        ("x.cc", True),
        ("x.cxx", True),
        ("x.hpp", True),
        ("x.py", False),
        ("x.txt", False),
        ("x", False),
    ],
)
def test_supports_treesitter(name, expected):
    assert ts.supports_treesitter(Path(name)) is expected


def test_parsed_function_definitions_lists_bodied_defs_in_order(make_source):
    src = make_source(
        "int add(int a, int b){ return a + b; }\n"
        "int decl(void);\n"  # declaration only -> excluded
        "void go(void){ }\n"
    )
    defs = ts.parsed_function_definitions(src)
    assert [d.name for d in defs] == ["add", "go"]
    assert defs[0].node.start_byte < defs[1].node.start_byte


def test_parsed_function_definitions_empty_without_functions(make_source):
    src = make_source("int global_value = 3;\n")
    assert ts.parsed_function_definitions(src) == []


def test_return_type_spelling_includes_type_and_pointer(make_source):
    src = make_source("static int *make_thing(void){ return 0; }\n")
    defs = ts.parsed_function_definitions(src)
    spelling = ts.return_type_spelling_from_definition(defs[0].node, src.content)
    assert spelling is not None
    assert "int" in spelling
    assert "*" in spelling


def test_load_language_rejects_unsupported_suffix():
    with pytest.raises(ts.TreeSitterParseError):
        ts.load_language(Path("x.rs"))

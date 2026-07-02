"""Tests for pseudoclang.locate (finding function body ranges)."""

from __future__ import annotations

import pytest

from pseudoclang.locate import FunctionLocateError, locate_function_body


def test_treesitter_happy_path(make_source):
    content = "int add(int a, int b){ return a + b; }\n"
    src = make_source(content, "m.c")

    loc = locate_function_body(src, "add")

    assert loc.function_name == "add"
    assert content[loc.opening_brace_index] == "{"
    assert content[loc.closing_brace_index] == "}"
    assert content[loc.body_start_index : loc.body_end_index].strip() == "return a + b;"
    assert loc.start_line == 1
    assert loc.end_line == 1


def test_locates_second_of_two_distinct_functions(make_source):
    content = "int a(void){ return 1; }\n\nint b(void){ return 2; }\n"
    src = make_source(content, "m.c")

    loc = locate_function_body(src, "b")

    assert content[loc.body_start_index : loc.body_end_index].strip() == "return 2;"
    assert loc.start_line == 3


def test_missing_function_raises(make_source):
    src = make_source("int add(void){ return 0; }\n", "m.c")
    with pytest.raises(FunctionLocateError):
        locate_function_body(src, "nope")


def test_ambiguous_function_raises(make_source):
    src = make_source("int f(void){ return 1; }\nint f(void){ return 2; }\n", "m.c")
    with pytest.raises(FunctionLocateError):
        locate_function_body(src, "f")


def test_declaration_only_raises(make_source):
    src = make_source("int f(void);\n", "m.c")
    with pytest.raises(FunctionLocateError):
        locate_function_body(src, "f")


def test_empty_name_raises(make_source):
    src = make_source("int f(void){ return 0; }\n", "m.c")
    with pytest.raises(FunctionLocateError):
        locate_function_body(src, "   ")


def test_locates_function_after_multibyte_comment(make_source):
    # A multi-byte comment before the function shifts byte offsets away from
    # character offsets; the located range must still be correct.
    content = "// \u00e9\u00e9\u00e9 \u00a9 \u20ac\nint add(int a, int b){ return a + b; }\n"
    src = make_source(content, "m.c")

    loc = locate_function_body(src, "add")

    assert content[loc.opening_brace_index] == "{"
    assert content[loc.closing_brace_index] == "}"
    assert content[loc.body_start_index : loc.body_end_index].strip() == "return a + b;"
    assert loc.start_line == 2


def test_locates_function_after_earlier_multibyte_function(make_source):
    content = (
        'const char *greet(void){ return "caf\u00e9 \u20ac"; }\n'
        "int add(int a, int b){ return a + b; }\n"
    )
    src = make_source(content, "m.c")

    loc = locate_function_body(src, "add")

    assert content[loc.opening_brace_index] == "{"
    assert content[loc.closing_brace_index] == "}"
    assert content[loc.body_start_index : loc.body_end_index].strip() == "return a + b;"
    assert loc.start_line == 2


def test_locates_function_with_multibyte_in_body(make_source):
    content = 'const char *msg(void){ return "\u00e9\u20ac\U0001f600"; }\n'
    src = make_source(content, "m.c")

    loc = locate_function_body(src, "msg")

    assert content[loc.opening_brace_index] == "{"
    assert content[loc.closing_brace_index] == "}"
    assert (
        content[loc.body_start_index : loc.body_end_index].strip()
        == 'return "\u00e9\u20ac\U0001f600";'
    )


def test_regex_fallback_for_unsupported_extension(make_source):
    # ".cu" is not a Tree-sitter suffix -> regex + brace-matching path.
    content = "int add(int a){ return a; }\n"
    src = make_source(content, "kernel.cu")

    loc = locate_function_body(src, "add")

    assert content[loc.body_start_index : loc.body_end_index].strip() == "return a;"


def test_regex_fallback_ignores_declaration(make_source):
    content = "int add(int a);\n"
    src = make_source(content, "kernel.cu")
    with pytest.raises(FunctionLocateError):
        locate_function_body(src, "add")

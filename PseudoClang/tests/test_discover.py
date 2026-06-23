"""Tests for pseudoclang.discover (sweep-mode function discovery)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pseudoclang.discover import (
    DiscoverError,
    discover_functions,
    validate_sweep_source_suffix,
)


def test_discovers_bodied_functions_in_order(make_source):
    content = "int a(void){ return 1; }\nvoid b(void){ }\nint c(void);\n"
    src = make_source(content, "s.c")

    found = discover_functions(src)

    assert [f.name for f in found] == ["a", "b"]  # c is a declaration -> excluded
    assert found[0].start_line == 1
    assert found[1].start_line == 2
    assert found[0].start_byte < found[1].start_byte
    assert found[0].end_byte > found[0].start_byte


def test_discovers_nothing_in_headerless_data(make_source):
    src = make_source("static const int TABLE[] = {1, 2, 3};\n", "s.c")
    assert discover_functions(src) == []


@pytest.mark.parametrize("name", ["x.c", "x.cpp", "x.cc", "x.cxx"])
def test_validate_sweep_suffix_accepts_sources(name):
    validate_sweep_source_suffix(Path(name))  # must not raise


@pytest.mark.parametrize("name", ["x.h", "x.hpp", "x.py", "x"])
def test_validate_sweep_suffix_rejects_others(name):
    with pytest.raises(DiscoverError):
        validate_sweep_source_suffix(Path(name))


def test_discover_rejects_header_file(make_source):
    src = make_source("int a(void){ return 1; }\n", "s.h")
    with pytest.raises(DiscoverError):
        discover_functions(src)

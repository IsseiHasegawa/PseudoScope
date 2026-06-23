"""Tests for pseudoclang.mutate (default-return mutations)."""

from __future__ import annotations

import pytest

from pseudoclang.locate import FunctionBodyLocation, locate_function_body
from pseudoclang.mutate import (
    MUTATION_TYPE_DEFAULT_RETURN,
    MutationError,
    generate_default_return_mutations,
    replacement_return_line,
    resolve_return_type_category,
)


def _locate(make_source, content, name="f", filename="m.c"):
    src = make_source(content, filename)
    return src, locate_function_body(src, name)


@pytest.mark.parametrize(
    "content,category",
    [
        ("void f(void){ work(); }", "void"),
        ("int f(void){ return 3; }", "integer"),
        ("long f(void){ return 3; }", "integer"),
        ("unsigned int f(void){ return 3; }", "integer"),
        ("float f(void){ return 1.5f; }", "float"),
        ("double f(void){ return 1.5; }", "double"),
        ("char f(void){ return 'x'; }", "char"),
        ("char *f(void){ return p; }", "pointer"),
    ],
)
def test_category_inference_c(make_source, content, category):
    src, loc = _locate(make_source, content)
    assert resolve_return_type_category(src, loc) == category


@pytest.mark.parametrize(
    "content,category",
    [
        ("bool f(){ return cond(); }", "bool"),
        ("std::string f(){ return s; }", "string"),
        ("std::vector<int> f(){ return v; }", "fallback"),
    ],
)
def test_category_inference_cpp(make_source, content, category):
    src, loc = _locate(make_source, content, filename="m.cpp")
    assert resolve_return_type_category(src, loc) == category


def test_void_yields_single_return_mutation(make_source):
    src, loc = _locate(make_source, "void f(void){ do_x(); }")
    muts = generate_default_return_mutations(src, loc)

    assert len(muts) == 1
    mutation = muts[0]
    assert mutation.return_type_category == "void"
    assert mutation.mutation_type == MUTATION_TYPE_DEFAULT_RETURN
    assert replacement_return_line(mutation.replacement_body) == "return;"
    assert mutation.original_body == " do_x(); "
    assert "do_x();" not in mutation.mutated_content
    assert mutation.mutated_content.startswith("void f(void){")
    assert mutation.mutated_content.rstrip().endswith("}")


def test_bool_yields_false_and_true(make_source):
    src, loc = _locate(make_source, "bool f(){ return g(); }", filename="m.cpp")
    lines = {replacement_return_line(m.replacement_body) for m in generate_default_return_mutations(src, loc)}
    assert lines == {"return false;", "return true;"}


def test_integer_yields_zero_and_one(make_source):
    src, loc = _locate(make_source, "int f(void){ return g(); }")
    lines = {replacement_return_line(m.replacement_body) for m in generate_default_return_mutations(src, loc)}
    assert lines == {"return 0;", "return 1;"}


def test_pointer_yields_nullptr(make_source):
    src, loc = _locate(make_source, "char *f(void){ return p; }")
    lines = {replacement_return_line(m.replacement_body) for m in generate_default_return_mutations(src, loc)}
    assert lines == {"return nullptr;"}


def test_mutation_only_replaces_the_body(make_source):
    content = "int add(int a){ return a + 1; }\n"
    src, loc = _locate(make_source, content, name="add")
    mutation = generate_default_return_mutations(src, loc)[0]

    # Splicing the original body back where the replacement went restores the file.
    start = loc.body_start_index
    rebuilt = (
        mutation.mutated_content[:start]
        + mutation.original_body
        + mutation.mutated_content[start + len(mutation.replacement_body) :]
    )
    assert rebuilt == content


def test_replacement_return_line_extracts_statement():
    assert replacement_return_line("\n    return 0;\n") == "return 0;"
    assert replacement_return_line("\n    return;\n") == "return;"


def test_validate_body_range_rejects_tampered_location(make_source):
    src, loc = _locate(make_source, "int f(void){ return 0; }")
    tampered = FunctionBodyLocation(
        **{**vars(loc), "opening_brace_index": loc.opening_brace_index + 5}
    )
    with pytest.raises(MutationError):
        generate_default_return_mutations(src, tampered)

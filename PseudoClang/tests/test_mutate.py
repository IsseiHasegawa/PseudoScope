"""Tests for pseudoclang.mutate (default-return mutations)."""

from __future__ import annotations

import pytest

from pseudoclang.locate import FunctionBodyLocation, locate_function_body
from pseudoclang.mutate import (
    MUTATION_TYPE_DEFAULT_RETURN,
    MutationError,
    UnsupportedReturnTypeError,
    _infer_return_type_category,
    generate_default_return_mutations,
    replacement_return_line,
    resolve_return_type_category,
)


def _locate(make_source, content, name="f", filename="m.c"):
    src = make_source(content, filename)
    return src, locate_function_body(src, name)


def _lines(src, loc):
    return {
        replacement_return_line(m.replacement_body)
        for m in generate_default_return_mutations(src, loc)
    }


def test_mutation_correct_after_multibyte_comment(make_source):
    # Byte offsets from Tree-sitter are shifted by the multi-byte comment; the
    # replaced body and the surrounding text must still be exact, and restoring
    # the original content must round-trip byte-for-byte.
    content = "// caf\u00e9 \u00a9 na\u00efve\nint add(int a, int b){ return a + b; }\n"
    src, loc = _locate(make_source, content, name="add")

    mutations = generate_default_return_mutations(src, loc)

    assert mutations
    for mutation in mutations:
        assert mutation.original_content == content
        # The comment (and its multi-byte characters) is preserved verbatim.
        assert mutation.mutated_content.startswith("// caf\u00e9 \u00a9 na\u00efve\n")
        # Only the body between the braces changed; the signature is intact.
        assert "int add(int a, int b){" in mutation.mutated_content
        assert "return a + b;" not in mutation.mutated_content


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
        ("unsigned f(void){ return 3; }", "integer"),
        ("signed f(void){ return 3; }", "integer"),
        ("char *f(void){ return p; }", "pointer"),
        ("struct Foo f(void){ return x; }", "fallback"),
        ("enum Color f(void){ return RED; }", "fallback"),
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
        ("std::unique_ptr<int> f(){ return p; }", "fallback"),
        ("std::shared_ptr<int> f(){ return p; }", "fallback"),
        ("std::optional<int> f(){ return o; }", "fallback"),
        # Wide/UTF strings are not std::string: fallback to {}, never "".
        ("std::wstring f(){ return s; }", "fallback"),
        ("std::u16string f(){ return s; }", "fallback"),
        # Templated/generic types whose arg contains a scalar keyword must not be
        # read as that scalar (vector<bool> is not bool; function<void()> not void).
        ("std::vector<bool> f(){ return v; }", "fallback"),
        ("std::function<void()> f(){ return g; }", "fallback"),
        ("std::function<void(int)> f(){ return g; }", "fallback"),
        ("std::tuple<int> f(){ return t; }", "fallback"),
        # A '*' / '&' inside a template argument belongs to that argument, not to
        # the return type: these stay default-constructible aggregates ({}).
        ("std::vector<int*> f(){ return v; }", "fallback"),
        ("std::vector<char *> f(){ return v; }", "fallback"),
        ("std::function<void(int*)> f(){ return g; }", "fallback"),
        ("std::function<void(int&)> f(){ return g; }", "fallback"),
        ("std::optional<char*> f(){ return o; }", "fallback"),
        ("std::pair<int, char*> f(){ return p; }", "fallback"),
        ("std::map<std::string, std::vector<int*>> f(){ return m; }", "fallback"),
        # A top-level '*' after the closing '>' is still a real pointer return.
        ("std::vector<int>* f(){ return p; }", "pointer"),
    ],
)
def test_category_inference_cpp(make_source, content, category):
    src, loc = _locate(make_source, content, filename="m.cpp")
    assert resolve_return_type_category(src, loc) == category


def test_infer_category_ignores_markers_inside_template_args():
    # Top-level '*' / '&' classify as pointer / reference.
    assert _infer_return_type_category("int *") == "pointer"
    assert _infer_return_type_category("std::vector<int>*") == "pointer"
    assert _infer_return_type_category("const T&") == "reference"
    assert _infer_return_type_category("int&&") == "reference"
    assert _infer_return_type_category("T*&") == "reference"
    # A '*' / '&' only inside '<...>' is a template argument, not a pointer/ref.
    assert _infer_return_type_category("std::vector<int*>") == "fallback"
    assert _infer_return_type_category("std::function<void(int&)>") == "fallback"
    assert _infer_return_type_category("std::map<std::string, std::vector<int*>>") == "fallback"


def test_stl_of_reference_is_mutated_not_excluded(make_source):
    # Regression: std::function<void(int&)> was misread as a reference return and
    # skipped entirely; the '&' is a template argument, so it must be mutated.
    src, loc = _locate(
        make_source, "std::function<void(int&)> f(){ return g; }", filename="m.cpp"
    )
    assert resolve_return_type_category(src, loc) == "fallback"
    assert _lines(src, loc) == {"return {};"}


# -- void --------------------------------------------------------------------


def test_void_yields_single_empty_body(make_source):
    src, loc = _locate(make_source, "void f(void){ do_x(); }")
    muts = generate_default_return_mutations(src, loc)

    assert len(muts) == 1
    mutation = muts[0]
    assert mutation.return_type_category == "void"
    assert mutation.mutation_type == MUTATION_TYPE_DEFAULT_RETURN
    # Empty body (no return value), not a `return;` statement.
    assert "return" not in mutation.replacement_body
    assert replacement_return_line(mutation.replacement_body) == "(empty body)"
    assert mutation.original_body == " do_x(); "
    assert "do_x();" not in mutation.mutated_content
    assert mutation.mutated_content.startswith("void f(void){")
    assert mutation.mutated_content.rstrip().endswith("}")


# -- numbers / char ----------------------------------------------------------


def test_integer_yields_zero_and_one(make_source):
    src, loc = _locate(make_source, "int f(void){ return g(); }")
    assert _lines(src, loc) == {"return 0;", "return 1;"}


def test_float_yields_zero_and_one_without_f_suffix(make_source):
    src, loc = _locate(make_source, "float f(void){ return 1.5f; }")
    assert _lines(src, loc) == {"return 0.0;", "return 1.0;"}


def test_double_yields_zero_and_one(make_source):
    src, loc = _locate(make_source, "double f(void){ return 1.5; }")
    assert _lines(src, loc) == {"return 0.0;", "return 1.0;"}


def test_char_yields_nul_and_a(make_source):
    src, loc = _locate(make_source, "char f(void){ return 'x'; }")
    assert _lines(src, loc) == {"return '\\0';", "return 'a';"}
    # Must never emit the multi-char constant '/0'.
    for m in generate_default_return_mutations(src, loc):
        assert "'/0'" not in m.replacement_body


# -- bool (C vs C++ / stdbool) ----------------------------------------------


def test_bool_cpp_yields_false_and_true(make_source):
    src, loc = _locate(make_source, "bool f(){ return g(); }", filename="m.cpp")
    assert _lines(src, loc) == {"return false;", "return true;"}


def test_bool_in_c_without_stdbool_yields_zero_one(make_source):
    src, loc = _locate(make_source, "bool f(void){ return g(); }", filename="m.c")
    assert _lines(src, loc) == {"return 0;", "return 1;"}


def test_bool_in_c_with_stdbool_yields_false_true(make_source):
    content = "#include <stdbool.h>\nbool f(void){ return g(); }"
    src, loc = _locate(make_source, content, filename="m.c")
    assert _lines(src, loc) == {"return false;", "return true;"}


# -- pointers (C vs C++) -----------------------------------------------------


def test_pointer_in_c_yields_null(make_source):
    src, loc = _locate(make_source, "char *f(void){ return p; }", filename="m.c")
    assert _lines(src, loc) == {"return NULL;"}


def test_pointer_in_cpp_yields_nullptr(make_source):
    src, loc = _locate(make_source, "char *f(){ return p; }", filename="m.cpp")
    assert _lines(src, loc) == {"return nullptr;"}


# -- C++ string / STL / smart ptr -------------------------------------------


def test_string_cpp_yields_empty_and_a(make_source):
    src, loc = _locate(make_source, "std::string f(){ return s; }", filename="m.cpp")
    assert _lines(src, loc) == {'return "";', 'return "A";'}


def test_stl_container_cpp_yields_braces(make_source):
    src, loc = _locate(make_source, "std::vector<int> f(){ return v; }", filename="m.cpp")
    assert _lines(src, loc) == {"return {};"}


def test_smart_pointer_cpp_yields_braces(make_source):
    src, loc = _locate(make_source, "std::unique_ptr<int> f(){ return p; }", filename="m.cpp")
    assert _lines(src, loc) == {"return {};"}


def test_wide_string_cpp_yields_braces_not_empty_string(make_source):
    # std::wstring is not constructible from "" ; it must fall back to {}.
    src, loc = _locate(make_source, "std::wstring f(){ return s; }", filename="m.cpp")
    assert _lines(src, loc) == {"return {};"}


def test_bare_unsigned_yields_zero_and_one(make_source):
    src, loc = _locate(make_source, "unsigned f(void){ return 3; }")
    assert _lines(src, loc) == {"return 0;", "return 1;"}


# -- C aggregates (struct / enum / typedef) ---------------------------------


def test_struct_in_c_yields_compound_literal(make_source):
    src, loc = _locate(make_source, "struct Foo f(void){ return x; }", filename="m.c")
    assert _lines(src, loc) == {"return (struct Foo){0};"}


def test_typedef_aggregate_in_c_yields_compound_literal(make_source):
    src, loc = _locate(make_source, "Widget f(void){ return x; }", filename="m.c")
    assert _lines(src, loc) == {"return (Widget){0};"}


def test_enum_in_c_yields_zero(make_source):
    src, loc = _locate(make_source, "enum Color f(void){ return RED; }", filename="m.c")
    assert _lines(src, loc) == {"return 0;"}


# -- excluded: references ----------------------------------------------------


def test_lvalue_reference_return_is_excluded(make_source):
    src, loc = _locate(make_source, "int& f(){ return g(); }", filename="m.cpp")
    assert resolve_return_type_category(src, loc) == "reference"
    with pytest.raises(UnsupportedReturnTypeError) as exc:
        generate_default_return_mutations(src, loc)
    assert exc.value.category == "reference"


def test_const_reference_return_is_excluded(make_source):
    # Tree-sitter drops the '&' here (spelling -> 'const'); reconciliation via
    # the raw signature text recovers it.
    src, loc = _locate(make_source, "const std::string& f(){ return s; }", filename="m.cpp")
    assert resolve_return_type_category(src, loc) == "reference"
    with pytest.raises(UnsupportedReturnTypeError):
        generate_default_return_mutations(src, loc)


def test_rvalue_reference_return_is_excluded(make_source):
    src, loc = _locate(make_source, "int&& f(){ return g(); }", filename="m.cpp")
    assert resolve_return_type_category(src, loc) == "reference"
    with pytest.raises(UnsupportedReturnTypeError):
        generate_default_return_mutations(src, loc)


# -- excluded / resolved: auto ----------------------------------------------


def test_auto_trailing_return_resolves(make_source):
    src, loc = _locate(make_source, "auto f() -> int { return 3; }", filename="m.cpp")
    assert resolve_return_type_category(src, loc) == "integer"
    assert _lines(src, loc) == {"return 0;", "return 1;"}


def test_auto_trailing_return_to_reference_is_excluded(make_source):
    src, loc = _locate(make_source, "auto f() -> int& { return g(); }", filename="m.cpp")
    assert resolve_return_type_category(src, loc) == "reference"
    with pytest.raises(UnsupportedReturnTypeError):
        generate_default_return_mutations(src, loc)


def test_bare_auto_is_excluded(make_source):
    src, loc = _locate(make_source, "auto f() { return 3; }", filename="m.cpp")
    assert resolve_return_type_category(src, loc) == "unresolved"
    with pytest.raises(UnsupportedReturnTypeError) as exc:
        generate_default_return_mutations(src, loc)
    assert exc.value.category == "unresolved"


def test_decltype_auto_is_excluded(make_source):
    src, loc = _locate(make_source, "decltype(auto) f() { return x; }", filename="m.cpp")
    assert resolve_return_type_category(src, loc) == "unresolved"
    with pytest.raises(UnsupportedReturnTypeError):
        generate_default_return_mutations(src, loc)


def test_decltype_expression_is_excluded_as_unresolved(make_source):
    # A '&' inside the decltype expression must not be read as a reference marker.
    src, loc = _locate(make_source, "decltype(a & b) f() { return x; }", filename="m.cpp")
    assert resolve_return_type_category(src, loc) == "unresolved"
    with pytest.raises(UnsupportedReturnTypeError):
        generate_default_return_mutations(src, loc)


def test_bare_auto_operator_arrow_is_excluded(make_source):
    # The '->' in operator-> must not be mistaken for a trailing-return arrow.
    src, loc = _locate(
        make_source, "auto operator->() { return ptr; }", name="operator->", filename="m.cpp"
    )
    assert resolve_return_type_category(src, loc) == "unresolved"
    with pytest.raises(UnsupportedReturnTypeError):
        generate_default_return_mutations(src, loc)


# -- invariants --------------------------------------------------------------


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
    assert replacement_return_line("\n") == "(empty body)"


def test_validate_body_range_rejects_tampered_location(make_source):
    src, loc = _locate(make_source, "int f(void){ return 0; }")
    tampered = FunctionBodyLocation(
        **{**vars(loc), "opening_brace_index": loc.opening_brace_index + 5}
    )
    with pytest.raises(MutationError):
        generate_default_return_mutations(src, tampered)

"""Tests for the UNCOMPILABLE status and exclusion plumbing (spec rule 6)."""

from __future__ import annotations

from pathlib import Path

from pseudoclang.analysis import analyze_function
from pseudoclang.executor import (
    STATUS_UNCOMPILABLE,
    MutationRunResult,
    _looks_like_compile_failure,
    _status_from_test_result,
)
from pseudoclang.models import PseudoScopeConfig
from pseudoclang.results import (
    _classification_payload,
    classify_function,
    display_status,
)
# Aliased so pytest does not try to collect the 'Test*'-named dataclass.
from pseudoclang.runner import TestRunResult as _RunnerTestResult


def _test_result(exit_code, *, stdout="", stderr="", timed_out=False):
    return _RunnerTestResult(
        test_command="pytest",
        project_root=".",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        runtime_seconds=0.1,
        timed_out=timed_out,
    )


def _mutation(status):
    return MutationRunResult(
        function_name="f",
        mutation_type="t",
        return_type_category="fallback",
        replacement_body="\n    return {};\n",
        exit_code=1,
        timed_out=False,
        stdout="",
        stderr="",
        runtime_seconds=0.1,
        status=status,
        restored=True,
    )


_CLANG = "objToJSON.c:42:10: error: no matching constructor for initialization of 'Foo'\n"
_GCC = "ujson.c:10: error: expected expression before '}' token\n"
_MSVC = "objToJSON.c(42): error C2440: cannot convert\n"


# -- executor: status classification ----------------------------------------


def test_compile_error_is_uncompilable_when_source_named():
    for blob, source_name in ((_CLANG, "objToJSON.c"), (_GCC, "ujson.c"), (_MSVC, "objToJSON.c")):
        result = _test_result(1, stderr=blob)
        assert _status_from_test_result(result, source_name=source_name) == STATUS_UNCOMPILABLE


def test_plain_test_failure_is_killed():
    result = _test_result(1, stderr="FAILED tests/test_x.py::test_y - assert 1 == 2")
    assert _status_from_test_result(result, source_name="objToJSON.c") == "killed"


def test_compile_diagnostic_for_other_file_is_killed():
    # A real kill whose output merely contains compiler-shaped text for an
    # unrelated file must NOT be relabeled uncompilable (filename gate).
    result = _test_result(1, stderr="other.c:3:5: error: expected ';'")
    assert _status_from_test_result(result, source_name="objToJSON.c") == "killed"


def test_exit_zero_is_survived():
    assert _status_from_test_result(_test_result(0), source_name="m.c") == "survived"


def test_timeout_is_timeout():
    assert _status_from_test_result(_test_result(None, timed_out=True), source_name="m.c") == "timeout"


def test_looks_like_compile_failure_requires_source_name():
    result = _test_result(1, stderr=_CLANG)
    assert _looks_like_compile_failure(result, "objToJSON.c") is True
    assert _looks_like_compile_failure(result, "unrelated.c") is False


# -- results: classification / denominator ----------------------------------


def test_classify_filters_uncompilable():
    assert classify_function([_mutation(STATUS_UNCOMPILABLE)]) == "not_analyzed"
    assert classify_function([_mutation("survived"), _mutation(STATUS_UNCOMPILABLE)]) == "pseudo_tested_candidate"
    assert classify_function([_mutation("killed"), _mutation(STATUS_UNCOMPILABLE)]) == "not_pseudo_tested"


def test_classification_payload_excludes_uncompilable_from_denominator():
    payload = _classification_payload(
        [_mutation("survived"), _mutation("killed"), _mutation(STATUS_UNCOMPILABLE)],
        label="x",
    )
    assert payload["total_mutations"] == 3
    assert payload["scored_mutations"] == 2
    assert payload["uncompilable"] == 1
    assert payload["survival_rate"] == 0.5  # 1 survived / 2 scored, not / 3


def test_display_status_labels_uncompilable():
    assert display_status(STATUS_UNCOMPILABLE) == "UNCOMPILABLE (skipped)"


# -- analysis: reference exclusion outcome -----------------------------------


def _config():
    return PseudoScopeConfig(
        project_root=Path("."),
        relative_file_path=Path("m.cpp"),
        target_file=Path("m.cpp"),
        function_name="f",
        test_command="pytest",
        output_path=Path("out.json"),
        timeout_seconds=60,
        mode=None,
        lang=None,
    )


def test_reference_return_function_is_skipped_and_labeled(make_source):
    src = make_source("int& f(){ return g(); }", "m.cpp")
    outcome = analyze_function(_config(), src, "f", run_mutations=True)
    assert outcome.status == "skipped"
    assert outcome.reason == "unsupported_return_type:reference"
    assert outcome.classification_override == "excluded_unsupported_return"
    assert outcome.mutation_results == []

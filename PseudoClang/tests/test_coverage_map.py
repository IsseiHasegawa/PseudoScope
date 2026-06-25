"""Tests for the pstrace coverage map loader, lookup, and selection policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pseudoclang.coverage_map import (
    JUDGMENT_FULL_ABSENT,
    JUDGMENT_FULL_STARTUP_ONLY,
    JUDGMENT_SELECTED,
    JUDGMENT_SKIPPED_UNCOVERED,
    CoverageMap,
    CoverageMapError,
    PlanKind,
    Selection,
    SelectionKind,
    decide_execution,
    load_coverage_map,
    verify_project_root,
)


def _map_document(project_root: str) -> dict:
    return {
        "meta": {
            "schema": "pstrace-coverage/1",
            "project_root": project_root,
            "image": "ujson.cpython-314-darwin.so",
            "created_at": "2026-06-24T12:00:00+00:00",
            "pstrace_version": "0.1.0",
        },
        "coverage": {
            "src/ujson/python/objToJSON.c": {
                "Object_beginTypeContext": [
                    "tests/test_ujson.py::test_encode_dict_values"
                ],
                "Dict_iterNext": [
                    "tests/test_ujson.py::test_dumps",
                    "tests/test_ujson.py::test_encode_dict_values",
                    "tests/test_ujson.py::test_dumps",  # duplicate on purpose
                ],
            },
            "src/ujson/python/ujson.c": {
                "PyInit_ujson": ["(startup)"],
                "module_traverse": [
                    "(startup)",
                    "tests/test_ujson.py::test_default_ref_counting",
                ],
            },
        },
        "tests": [
            "tests/test_ujson.py::test_dumps",
            "tests/test_ujson.py::test_encode_dict_values",
            "tests/test_ujson.py::test_default_ref_counting",
        ],
    }


def _write_map(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_load_none_returns_none():
    assert load_coverage_map(None) is None


def test_load_valid_map(tmp_path):
    path = _write_map(tmp_path, _map_document(str(tmp_path)))
    cov = load_coverage_map(path)
    assert isinstance(cov, CoverageMap)
    assert cov.image == "ujson.cpython-314-darwin.so"
    assert cov.created_at == "2026-06-24T12:00:00+00:00"
    assert cov.universe() == {
        "tests/test_ujson.py::test_dumps",
        "tests/test_ujson.py::test_encode_dict_values",
        "tests/test_ujson.py::test_default_ref_counting",
    }


def test_lookup_selected_dedupes_and_sorts(tmp_path):
    cov = load_coverage_map(_write_map(tmp_path, _map_document(str(tmp_path))))
    sel = cov.lookup("src/ujson/python/objToJSON.c", "Dict_iterNext")
    assert sel.kind is SelectionKind.SELECTED
    assert sel.nodeids == (
        "tests/test_ujson.py::test_dumps",
        "tests/test_ujson.py::test_encode_dict_values",
    )


def test_lookup_startup_only(tmp_path):
    cov = load_coverage_map(_write_map(tmp_path, _map_document(str(tmp_path))))
    sel = cov.lookup("src/ujson/python/ujson.c", "PyInit_ujson")
    assert sel.kind is SelectionKind.STARTUP_ONLY
    assert sel.nodeids == ()


def test_lookup_startup_plus_test_is_selected_without_startup(tmp_path):
    cov = load_coverage_map(_write_map(tmp_path, _map_document(str(tmp_path))))
    sel = cov.lookup("src/ujson/python/ujson.c", "module_traverse")
    assert sel.kind is SelectionKind.SELECTED
    assert "(startup)" not in sel.nodeids
    assert sel.nodeids == ("tests/test_ujson.py::test_default_ref_counting",)


def test_lookup_absent_function(tmp_path):
    cov = load_coverage_map(_write_map(tmp_path, _map_document(str(tmp_path))))
    assert cov.lookup("src/ujson/python/objToJSON.c", "nope").kind is SelectionKind.ABSENT


def test_lookup_absent_file(tmp_path):
    cov = load_coverage_map(_write_map(tmp_path, _map_document(str(tmp_path))))
    assert cov.lookup("src/other.c", "anything").kind is SelectionKind.ABSENT


def test_lookup_accepts_absolute_path(tmp_path):
    cov = load_coverage_map(_write_map(tmp_path, _map_document(str(tmp_path))))
    abs_path = Path(tmp_path) / "src/ujson/python/objToJSON.c"
    sel = cov.lookup(abs_path, "Object_beginTypeContext")
    assert sel.kind is SelectionKind.SELECTED


def test_schema_mismatch_is_error(tmp_path):
    doc = _map_document(str(tmp_path))
    doc["meta"]["schema"] = "pstrace-coverage/2"
    with pytest.raises(CoverageMapError, match="schema"):
        load_coverage_map(_write_map(tmp_path, doc))


def test_malformed_json_is_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CoverageMapError, match="valid JSON"):
        load_coverage_map(path)


def test_missing_meta_is_error(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"coverage": {}, "tests": []}), encoding="utf-8")
    with pytest.raises(CoverageMapError, match="meta"):
        load_coverage_map(path)


def test_wrong_coverage_type_is_error(tmp_path):
    doc = _map_document(str(tmp_path))
    doc["coverage"] = ["not", "a", "dict"]
    with pytest.raises(CoverageMapError, match="coverage"):
        load_coverage_map(_write_map(tmp_path, doc))


def test_wrong_tests_type_is_error(tmp_path):
    doc = _map_document(str(tmp_path))
    doc["tests"] = "not-a-list"
    with pytest.raises(CoverageMapError, match="tests"):
        load_coverage_map(_write_map(tmp_path, doc))


def test_missing_file_is_error(tmp_path):
    with pytest.raises(CoverageMapError, match="Cannot read"):
        load_coverage_map(tmp_path / "does-not-exist.json")


# -- verify_project_root ----------------------------------------------------


def test_verify_project_root_match(tmp_path):
    cov = load_coverage_map(_write_map(tmp_path, _map_document(str(tmp_path))))
    verify_project_root(cov, tmp_path)  # no raise


def test_verify_project_root_mismatch(tmp_path):
    cov = load_coverage_map(_write_map(tmp_path, _map_document("/some/other/root")))
    with pytest.raises(CoverageMapError, match="different project root"):
        verify_project_root(cov, tmp_path)


# -- decide_execution policy ------------------------------------------------


def test_decide_selected_runs_subset():
    sel = Selection(SelectionKind.SELECTED, ("a", "b"))
    plan = decide_execution(sel, assume_complete=False)
    assert plan.kind is PlanKind.RUN_SELECTED
    assert plan.judgment == JUDGMENT_SELECTED
    assert plan.nodeids == ("a", "b")


def test_decide_startup_only_runs_full():
    plan = decide_execution(Selection(SelectionKind.STARTUP_ONLY), assume_complete=False)
    assert plan.kind is PlanKind.RUN_FULL
    assert plan.judgment == JUDGMENT_FULL_STARTUP_ONLY


def test_decide_startup_only_never_skipped_even_when_complete():
    plan = decide_execution(Selection(SelectionKind.STARTUP_ONLY), assume_complete=True)
    assert plan.kind is PlanKind.RUN_FULL
    assert plan.judgment == JUDGMENT_FULL_STARTUP_ONLY


def test_decide_absent_default_runs_full():
    plan = decide_execution(Selection(SelectionKind.ABSENT), assume_complete=False)
    assert plan.kind is PlanKind.RUN_FULL
    assert plan.judgment == JUDGMENT_FULL_ABSENT


def test_decide_absent_complete_skips_as_survived():
    plan = decide_execution(Selection(SelectionKind.ABSENT), assume_complete=True)
    assert plan.kind is PlanKind.SKIP_AS_SURVIVED
    assert plan.judgment == JUDGMENT_SKIPPED_UNCOVERED

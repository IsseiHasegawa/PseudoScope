"""Tests for --coverage-map-cmd auto-generation (Option A orchestration)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pseudoclang.cli import generate_coverage_map, load_coverage_map_for_run
from pseudoclang.coverage_map import CoverageMap, CoverageMapError
from pseudoclang.models import ConfigError, PseudoScopeConfig
from pseudoclang.validation import build_config


def _writer_cmd(project_root: Path, *, tests=("t::a",)) -> str:
    """A shell command that writes a valid pstrace-coverage/1 map to the env path."""
    doc = json.dumps(
        {
            "meta": {"schema": "pstrace-coverage/1", "project_root": str(project_root.resolve())},
            "coverage": {},
            "tests": list(tests),
        }
    )
    return f"printf '%s' '{doc}' > \"$PSEUDOCLANG_COVERAGE_MAP\""


def _config(tmp_path: Path, cmd: str | None, *, refresh: bool = False, map_name="cov.json"):
    return PseudoScopeConfig(
        project_root=tmp_path,
        relative_file_path=Path("src/x.c"),
        target_file=tmp_path / "src" / "x.c",
        function_name=None,
        test_command="pytest",
        output_path=tmp_path / "out.json",
        timeout_seconds=60,
        mode=None,
        lang=None,
        coverage_map_path=tmp_path / map_name,
        coverage_map_cmd=cmd,
        refresh_coverage_map=refresh,
    )


# -- generation + loading ----------------------------------------------------


def test_generate_writes_map_and_sets_env(tmp_path):
    cfg = _config(tmp_path, _writer_cmd(tmp_path))
    generate_coverage_map(cfg)
    assert cfg.coverage_map_path.exists()
    doc = json.loads(cfg.coverage_map_path.read_text())
    assert doc["meta"]["schema"] == "pstrace-coverage/1"


def test_load_generates_when_absent(tmp_path):
    cfg = _config(tmp_path, _writer_cmd(tmp_path))
    assert not cfg.coverage_map_path.exists()
    cov = load_coverage_map_for_run(cfg)
    assert isinstance(cov, CoverageMap)
    assert cfg.coverage_map_path.exists()


def test_load_reuses_existing_without_running_cmd(tmp_path):
    cfg = _config(tmp_path, "exit 7")  # cmd would fail if ever run
    cfg.coverage_map_path.write_text(
        json.dumps(
            {
                "meta": {"schema": "pstrace-coverage/1", "project_root": str(tmp_path.resolve())},
                "coverage": {},
                "tests": [],
            }
        )
    )
    cov = load_coverage_map_for_run(cfg)  # must NOT invoke the failing cmd
    assert isinstance(cov, CoverageMap)


def test_refresh_regenerates_even_if_present(tmp_path):
    cfg = _config(tmp_path, _writer_cmd(tmp_path, tests=("t::fresh",)), refresh=True)
    cfg.coverage_map_path.write_text(
        json.dumps(
            {
                "meta": {"schema": "pstrace-coverage/1", "project_root": str(tmp_path.resolve())},
                "coverage": {},
                "tests": ["t::stale"],
            }
        )
    )
    cov = load_coverage_map_for_run(cfg)
    assert cov.universe() == {"t::fresh"}  # regenerated, not the stale file


def test_nonzero_exit_is_fatal(tmp_path):
    cfg = _config(tmp_path, "exit 3")
    with pytest.raises(CoverageMapError, match="failed"):
        generate_coverage_map(cfg)


def test_exit_zero_without_output_is_fatal(tmp_path):
    cfg = _config(tmp_path, "true")  # exits 0 but writes nothing
    with pytest.raises(CoverageMapError, match="no map"):
        generate_coverage_map(cfg)


# -- validation --------------------------------------------------------------


def test_cmd_requires_coverage_map_path(tmp_path):
    with pytest.raises(ConfigError, match="requires --coverage-map"):
        build_config(
            project_root_source_dir=str(tmp_path),
            file=None,
            function=None,
            test_command="pytest",
            output_dir=None,
            output_file=None,
            timeout=60,
            mode=None,
            lang=None,
            coverage_map_cmd="bash recipe.sh",
        )


def test_refresh_requires_cmd(tmp_path):
    with pytest.raises(ConfigError, match="requires --coverage-map-cmd"):
        build_config(
            project_root_source_dir=str(tmp_path),
            file=None,
            function=None,
            test_command="pytest",
            output_dir=None,
            output_file=None,
            timeout=60,
            mode=None,
            lang=None,
            coverage_map="cov.json",
            refresh_coverage_map=True,
        )

"""Tests for PseudoClang building the pstrace coverage-map command itself."""

from __future__ import annotations

import pytest

from pseudoclang.models import ConfigError
from pseudoclang.pstrace_integration import (
    build_pstrace_coverage_map_cmd,
    default_pstrace_repo,
)
from pseudoclang.validation import build_config


def _bc(tmp_path, **kw):
    base = dict(
        project_root_source_dir=str(tmp_path),
        file=None,
        function=None,
        test_command="pytest",
        output_dir=None,
        output_file=None,
        timeout=60,
        mode=None,
        lang=None,
    )
    base.update(kw)
    return build_config(**base)


def test_default_repo_is_the_sibling_checkout():
    repo = default_pstrace_repo()
    assert repo.name == "pstrace"
    assert (repo / "pstrace" / "driver.py").is_file()  # sibling pstrace is present


def test_command_shape(tmp_path):
    cmd = build_pstrace_coverage_map_cmd(
        project_root=tmp_path,
        module="ujson",
        src_root="src/ujson",
        build_cmd="pip install -e .",
        test_cmd="python -m pytest",
    )
    assert "-m pstrace.driver" in cmd
    assert "--module ujson" in cmd
    assert "--build-cmd 'pip install -e .'" in cmd
    assert "PYTHONPATH=" in cmd
    # the map var must stay shell-expandable (unquoted variable)
    assert '--coverage-json "$PSEUDOCLANG_COVERAGE_MAP"' in cmd


def test_missing_pstrace_checkout_raises(tmp_path):
    with pytest.raises(ConfigError):
        build_pstrace_coverage_map_cmd(
            project_root=tmp_path, module="m", src_root="s",
            build_cmd="b", test_cmd="t", repo=str(tmp_path),  # no pstrace pkg here
        )


def test_build_config_sets_cmd_and_defaults_map_path(tmp_path):
    cfg = _bc(
        tmp_path,
        pstrace_module="ujson",
        pstrace_src_root="src/ujson",
        pstrace_build_cmd="pip install -e .",
        pstrace_test_cmd="python -m pytest",
        test_runner_template="python -m pytest {selection}",
    )
    assert cfg.coverage_map_cmd and "pstrace.driver" in cfg.coverage_map_cmd
    assert cfg.coverage_map_path is not None  # defaulted since --coverage-map omitted
    assert cfg.coverage_map_path.name == "coverage-map.json"


def test_build_config_requires_subflags(tmp_path):
    with pytest.raises(ConfigError):
        _bc(tmp_path, pstrace_module="ujson")  # missing src-root/build-cmd/test-cmd


def test_build_config_pstrace_conflicts_with_coverage_map_cmd(tmp_path):
    with pytest.raises(ConfigError):
        _bc(
            tmp_path,
            pstrace_module="ujson",
            pstrace_src_root="s",
            pstrace_build_cmd="b",
            pstrace_test_cmd="t",
            coverage_map="cov.json",
            coverage_map_cmd="echo hi",
        )

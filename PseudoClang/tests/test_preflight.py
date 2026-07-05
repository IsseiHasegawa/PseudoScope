"""Tests for the --test-runner-template rebuild preflight guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from pseudoclang.coverage_map import CoverageMap
from pseudoclang.models import PseudoScopeConfig
from pseudoclang.preflight import (
    PreflightError,
    check_map_covers_current_tests,
    check_test_command_rebuilds,
    check_test_runner_rebuilds,
)


def _config(tmp_path: Path, template: str) -> PseudoScopeConfig:
    target = tmp_path / "foo.c"
    target.write_text("int f(void){ return 0; }\n", encoding="utf-8")
    return PseudoScopeConfig(
        project_root=tmp_path,
        relative_file_path=Path("foo.c"),
        target_file=target,
        function_name=None,
        test_command="true",
        output_path=tmp_path / "out.json",
        timeout_seconds=30,
        mode=None,
        lang=None,
        coverage_map_path=tmp_path / "cov.json",
        test_runner_template=template,
    )


def _map(tests: list[str]) -> CoverageMap:
    return CoverageMap(
        coverage={},
        tests=tests,
        meta={"schema": "pstrace-coverage/1", "project_root": "/x"},
    )


def test_flags_template_that_does_not_rebuild(tmp_path):
    # A template that ignores the source (always exits 0) is the stale-binary
    # footgun: with a compile error injected it still "passes".
    cfg = _config(tmp_path, "true  # {selection}")
    with pytest.raises(PreflightError):
        check_test_runner_rebuilds(cfg, _map(["t1"]))
    assert "#error" not in cfg.target_file.read_text(encoding="utf-8")  # restored


def test_accepts_template_that_rebuilds(tmp_path):
    # This template fails (exit 1) whenever the canary is present in foo.c,
    # standing in for a build that fails to compile the mutated source.
    cfg = _config(tmp_path, "! grep -q pstrace_template_rebuild_check foo.c  # {selection}")
    check_test_runner_rebuilds(cfg, _map(["t1"]))  # must not raise
    assert "#error" not in cfg.target_file.read_text(encoding="utf-8")  # restored


def test_noop_when_map_has_no_tests(tmp_path):
    cfg = _config(tmp_path, "true  # {selection}")
    check_test_runner_rebuilds(cfg, _map([]))  # nothing to probe -> no raise


def test_registers_with_backstop_during_the_template_run(tmp_path, monkeypatch):
    # While the template runs, the canary is on disk AND registered with the
    # SIGTERM/atexit backstop; afterwards the source is restored and unregistered.
    from pseudoclang import restore_backstop
    from pseudoclang.runner import TestRunResult

    cfg = _config(tmp_path, "irrelevant  # {selection}")
    seen: dict = {}

    def fake_run(config, nodeids):
        seen["registered"] = config.target_file in restore_backstop._PENDING_RESTORES
        seen["content"] = config.target_file.read_text(encoding="utf-8")
        return TestRunResult(
            test_command="x", project_root=str(tmp_path), exit_code=1,
            stdout="", stderr="", runtime_seconds=0.0, timed_out=False,
        )

    monkeypatch.setattr("pseudoclang.preflight.run_selected_test_command", fake_run)
    check_test_runner_rebuilds(cfg, _map(["t1"]))  # exit 1 -> not the footgun

    assert seen["registered"] is True
    assert "pstrace_template_rebuild_check" in seen["content"]  # canary was on disk
    assert cfg.target_file.read_text(encoding="utf-8") == "int f(void){ return 0; }\n"
    assert cfg.target_file not in restore_backstop._PENDING_RESTORES


def test_restores_source_even_when_template_errors(tmp_path):
    # A template that cannot start still leaves the source restored.
    cfg = _config(tmp_path, "this_command_does_not_exist_zzz {selection}")
    # Non-zero exit (command not found) is treated as "reacted to the change",
    # so no PreflightError; the file must be restored regardless.
    check_test_runner_rebuilds(cfg, _map(["t1"]))
    assert cfg.target_file.read_text(encoding="utf-8") == "int f(void){ return 0; }\n"


# -- #2: the full --test-command rebuild guard ------------------------------


def _cmd_config(tmp_path: Path, test_command: str, **overrides) -> PseudoScopeConfig:
    target = tmp_path / "foo.c"
    target.write_text("int f(void){ return 0; }\n", encoding="utf-8")
    fields = dict(
        project_root=tmp_path,
        relative_file_path=Path("foo.c"),
        target_file=target,
        function_name=None,
        test_command=test_command,
        output_path=tmp_path / "out.json",
        timeout_seconds=30,
        mode=None,
        lang=None,
    )
    fields.update(overrides)
    return PseudoScopeConfig(**fields)


def test_flags_test_command_that_does_not_rebuild(tmp_path):
    # A --test-command that ignores the source (always exits 0) tests a stale
    # binary: with a compile error injected it still "passes".
    cfg = _cmd_config(tmp_path, "true")
    with pytest.raises(PreflightError):
        check_test_command_rebuilds(cfg)
    assert "#error" not in cfg.target_file.read_text(encoding="utf-8")  # restored


def test_accepts_test_command_that_rebuilds(tmp_path):
    # This command fails (exit 1) whenever the canary is present in foo.c,
    # standing in for a build that fails to compile the mutated source.
    cfg = _cmd_config(tmp_path, "! grep -q pstrace_template_rebuild_check foo.c")
    check_test_command_rebuilds(cfg)  # must not raise
    assert "#error" not in cfg.target_file.read_text(encoding="utf-8")  # restored


def test_test_command_probe_is_noop_without_target(tmp_path):
    cfg = _cmd_config(tmp_path, "true")
    object.__setattr__(cfg, "target_file", None)
    check_test_command_rebuilds(cfg)  # no target -> nothing to probe, no raise


def test_test_command_probe_restores_when_command_missing(tmp_path):
    # A command that cannot start (exit 127 via the shell) is treated as
    # "reacted to the change"; the source must be restored regardless.
    cfg = _cmd_config(tmp_path, "this_command_does_not_exist_zzz")
    check_test_command_rebuilds(cfg)
    assert cfg.target_file.read_text(encoding="utf-8") == "int f(void){ return 0; }\n"


# -- #1: coverage-map freshness advisory / new-test check (warn-only) --------


def _freshness_config(
    tmp_path: Path, test_list_cmd: str | None
) -> PseudoScopeConfig:
    target = tmp_path / "foo.c"
    target.write_text("int f(void){ return 0; }\n", encoding="utf-8")
    return PseudoScopeConfig(
        project_root=tmp_path,
        relative_file_path=Path("foo.c"),
        target_file=target,
        function_name=None,
        test_command="true",
        output_path=tmp_path / "out.json",
        timeout_seconds=30,
        mode=None,
        lang=None,
        coverage_map_path=tmp_path / "cov.json",
        test_runner_template="pytest {selection}",
        test_list_cmd=test_list_cmd,
    )


def test_map_freshness_advisory_without_list_cmd(tmp_path, capsys):
    cfg = _freshness_config(tmp_path, None)
    check_map_covers_current_tests(cfg, _map(["a", "b"]))  # never raises
    out = capsys.readouterr()
    assert "reflect the test suite recorded in the coverage map" in out.err
    assert "accounts for all" not in out.out  # no lister was run


def test_map_freshness_warns_on_new_test(tmp_path, capsys):
    cfg = _freshness_config(tmp_path, r"printf 'a\nb\nc\n'")
    check_map_covers_current_tests(cfg, _map(["a", "b"]))  # never raises
    err = capsys.readouterr().err
    assert "1 test(s) in the current suite are absent from the coverage map" in err
    assert "c" in err


def test_map_freshness_ok_when_no_new_tests(tmp_path, capsys):
    cfg = _freshness_config(tmp_path, r"printf 'a\nb\n'")
    check_map_covers_current_tests(cfg, _map(["a", "b"]))
    out = capsys.readouterr()
    assert "absent from the coverage map" not in out.err
    assert "accounts for all 2 current" in out.out


def test_map_freshness_ignores_removed_tests(tmp_path, capsys):
    # A test present in the map but gone from the live suite cannot kill a
    # mutant, so it must not be flagged.
    cfg = _freshness_config(tmp_path, r"printf 'a\n'")
    check_map_covers_current_tests(cfg, _map(["a", "b"]))
    assert "absent from the coverage map" not in capsys.readouterr().err


def test_map_freshness_strips_blank_lines(tmp_path, capsys):
    cfg = _freshness_config(tmp_path, r"printf 'a\n\n b \n'")
    check_map_covers_current_tests(cfg, _map(["a", "b"]))
    out = capsys.readouterr()
    assert "absent from the coverage map" not in out.err
    assert "accounts for all 2 current" in out.out

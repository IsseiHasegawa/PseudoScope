"""Tests for the selected-survivor confirmation pass and the selected-subset guard.

Covers the default-on safety re-check that turns a map-selected verdict into a
full-suite-trustworthy one: a subset survivor is re-judged against the full
``--test-command`` (correcting a false pseudo-tested verdict when the map missed a
covering test), and a subset that no longer passes on the original source falls
back to the full suite (fixing a silent false not-pseudo-tested verdict).
"""

from __future__ import annotations

from pathlib import Path

from pseudoclang.analysis import guard_selected_plan
from pseudoclang.coverage_map import (
    JUDGMENT_FULL_NO_MAP,
    JUDGMENT_FULL_SELECTED_UNTRUSTWORTHY,
    JUDGMENT_SELECTED,
    ExecutionPlan,
    PlanKind,
)
from pseudoclang.cli import build_parser, normalize_argv
from pseudoclang.executor import (
    MutationRunResult,
    combine_confirmation,
    run_single_mutation_test,
)
from pseudoclang.locate import locate_function_body
from pseudoclang.models import PseudoScopeConfig
from pseudoclang.mutate import generate_default_return_mutations
from pseudoclang.results import classify_function, _mutation_payload
from pseudoclang.source import SourceFile
from pseudoclang.validation import build_config

_C_SOURCE = "int add(int a, int b){ return a + b; }\n"
_SELECTED = ("tests/t.py::a",)


def _config(tmp_path: Path, target: Path, **overrides) -> PseudoScopeConfig:
    fields = dict(
        project_root=tmp_path,
        relative_file_path=Path(target.name),
        target_file=target,
        function_name="add",
        test_command="true",
        output_path=tmp_path / "out.json",
        timeout_seconds=60,
        mode=None,
        lang=None,
    )
    fields.update(overrides)
    return PseudoScopeConfig(**fields)


def _mutation(tmp_path: Path):
    f = tmp_path / "x.c"
    f.write_text(_C_SOURCE)
    src = SourceFile(
        path=f, relative_path=Path("x.c"), content=_C_SOURCE, encoding="utf-8", line_count=1
    )
    loc = locate_function_body(src, "add")
    return f, generate_default_return_mutations(src, loc)[0]


def _selected_plan(nodeids=_SELECTED) -> ExecutionPlan:
    return ExecutionPlan(PlanKind.RUN_SELECTED, judgment=JUDGMENT_SELECTED, nodeids=nodeids)


# -- combine_confirmation (pure truth table) --------------------------------


def test_combine_no_confirmation_run():
    # full_status None -> the selected verdict stands, unlabeled.
    for sel in ("killed", "timeout", "uncompilable", "survived"):
        assert combine_confirmation(sel, None) == (sel, None)


def test_combine_confirmed_survived():
    assert combine_confirmation("survived", "survived") == ("survived", "confirmed_survived")


def test_combine_corrected_killed():
    assert combine_confirmation("survived", "killed") == ("killed", "corrected_killed")


def test_combine_inconclusive_timeout():
    assert combine_confirmation("survived", "timeout") == ("timeout", "inconclusive_timeout")


def test_combine_confirmation_uncompilable():
    assert combine_confirmation("survived", "uncompilable") == (
        "uncompilable",
        "confirmation_uncompilable",
    )


# -- run_single_mutation_test confirmation ----------------------------------


def test_survivor_corrected_to_killed_by_full_suite(tmp_path):
    # Subset passes (weak), full suite fails: the map missed a covering test.
    f, mutation = _mutation(tmp_path)
    config = _config(
        tmp_path, f, test_runner_template="true {selection}", test_command="false"
    )
    result = run_single_mutation_test(config, mutation, execution_plan=_selected_plan())

    assert result.status == "killed"
    assert result.selected_status == "survived"
    assert result.confirmation == "corrected_killed"
    assert result.restored is True
    assert f.read_text() == _C_SOURCE  # restored byte-for-byte


def test_survivor_confirmed_by_full_suite(tmp_path):
    f, mutation = _mutation(tmp_path)
    config = _config(
        tmp_path, f, test_runner_template="true {selection}", test_command="true"
    )
    result = run_single_mutation_test(config, mutation, execution_plan=_selected_plan())

    assert result.status == "survived"
    assert result.selected_status == "survived"
    assert result.confirmation == "confirmed_survived"


def test_confirmation_disabled_trusts_subset(tmp_path):
    # With confirmation off, a subset survivor is trusted even though the full
    # command would have killed it (proving the full run did not execute).
    f, mutation = _mutation(tmp_path)
    config = _config(
        tmp_path,
        f,
        test_runner_template="true {selection}",
        test_command="false",
        confirm_survivors=False,
    )
    result = run_single_mutation_test(config, mutation, execution_plan=_selected_plan())

    assert result.status == "survived"
    assert result.confirmation is None
    assert result.selected_status is None


def test_killed_subset_is_not_reconfirmed(tmp_path):
    # A subset that kills the mutant needs no confirmation; the full command
    # (which would create the marker) must never run.
    f, mutation = _mutation(tmp_path)
    marker = tmp_path / "full_ran.marker"
    config = _config(
        tmp_path,
        f,
        test_runner_template="false {selection}",
        test_command=f"touch {marker}",
    )
    result = run_single_mutation_test(config, mutation, execution_plan=_selected_plan())

    assert result.status == "killed"
    assert result.confirmation is None
    assert not marker.exists()  # confirmation full run did not fire


def test_confirmation_timeout_is_inconclusive(tmp_path):
    f, mutation = _mutation(tmp_path)
    config = _config(
        tmp_path,
        f,
        test_runner_template="true {selection}",
        test_command="sleep 5",
        timeout_seconds=1,
    )
    result = run_single_mutation_test(config, mutation, execution_plan=_selected_plan())

    assert result.status == "timeout"
    assert result.confirmation == "inconclusive_timeout"


def test_full_run_plan_has_no_confirmation(tmp_path):
    # A RUN_FULL plan (no execution_plan) never records confirmation provenance.
    f, mutation = _mutation(tmp_path)
    config = _config(tmp_path, f, test_command="true")
    result = run_single_mutation_test(config, mutation, execution_plan=None)

    assert result.status == "survived"
    assert result.confirmation is None
    assert result.selected_status is None


# -- guard_selected_plan ----------------------------------------------------


def test_guard_degrades_when_subset_fails(tmp_path, capsys):
    f = tmp_path / "x.c"
    f.write_text(_C_SOURCE)
    config = _config(tmp_path, f, test_runner_template="false {selection}")
    plan = guard_selected_plan(config, _selected_plan(), "add")

    assert plan.kind is PlanKind.RUN_FULL
    assert plan.judgment == JUDGMENT_FULL_SELECTED_UNTRUSTWORTHY
    err = capsys.readouterr().err
    assert "selected tests for add do not pass" in err


def test_guard_keeps_plan_when_subset_passes(tmp_path):
    f = tmp_path / "x.c"
    f.write_text(_C_SOURCE)
    config = _config(tmp_path, f, test_runner_template="true {selection}")
    plan = guard_selected_plan(config, _selected_plan(), "add")

    assert plan.kind is PlanKind.RUN_SELECTED
    assert plan.nodeids == _SELECTED


def test_guard_is_noop_for_full_plan(tmp_path):
    f = tmp_path / "x.c"
    f.write_text(_C_SOURCE)
    # Template would fail if run; a RUN_FULL plan must not run it (early return).
    config = _config(tmp_path, f, test_runner_template="false {selection}")
    full = ExecutionPlan(PlanKind.RUN_FULL, judgment=JUDGMENT_FULL_NO_MAP)
    assert guard_selected_plan(config, full, "add") is full


def test_guard_is_noop_when_confirmation_disabled(tmp_path):
    f = tmp_path / "x.c"
    f.write_text(_C_SOURCE)
    config = _config(
        tmp_path, f, test_runner_template="false {selection}", confirm_survivors=False
    )
    plan = _selected_plan()
    assert guard_selected_plan(config, plan, "add") is plan


# -- JSON payload + classification ------------------------------------------


def _result(status, *, selected_status=None, confirmation=None) -> MutationRunResult:
    return MutationRunResult(
        function_name="f",
        mutation_type="default_return",
        return_type_category="integer",
        replacement_body="return 0;",
        exit_code=0,
        timed_out=False,
        stdout="",
        stderr="",
        runtime_seconds=0.0,
        status=status,
        restored=True,
        selected_status=selected_status,
        confirmation=confirmation,
    )


def test_payload_surfaces_confirmation_when_present():
    payload = _mutation_payload(
        _result("killed", selected_status="survived", confirmation="corrected_killed")
    )
    assert payload["status"] == "killed"
    assert payload["selected_status"] == "survived"
    assert payload["confirmation"] == "corrected_killed"


def test_payload_omits_confirmation_when_absent():
    payload = _mutation_payload(_result("survived"))
    assert "confirmation" not in payload
    assert "selected_status" not in payload


def test_corrected_killed_classifies_as_not_pseudo_tested():
    corrected = _result("killed", selected_status="survived", confirmation="corrected_killed")
    assert classify_function([corrected]) == "not_pseudo_tested"


def test_confirmed_and_corrected_mix_is_partial():
    results = [
        _result("survived", selected_status="survived", confirmation="confirmed_survived"),
        _result("killed", selected_status="survived", confirmation="corrected_killed"),
    ]
    assert classify_function(results) == "partially_tested"


# -- config / CLI flag wiring -----------------------------------------------


def test_build_config_confirm_survivors_defaults_on(tmp_path):
    cfg = build_config(
        project_root_source_dir=str(tmp_path),
        file=None,
        function=None,
        test_command="pytest",
        output_dir=None,
        output_file=None,
        timeout=30,
        mode=None,
        lang=None,
    )
    assert cfg.confirm_survivors is True


def test_build_config_confirm_survivors_can_be_disabled(tmp_path):
    cfg = build_config(
        project_root_source_dir=str(tmp_path),
        file=None,
        function=None,
        test_command="pytest",
        output_dir=None,
        output_file=None,
        timeout=30,
        mode=None,
        lang=None,
        confirm_survivors=False,
    )
    assert cfg.confirm_survivors is False


def test_cli_flag_defaults_on():
    parser = build_parser()
    args = parser.parse_args(
        normalize_argv(
            ["--project-root-source-dir", "x", "--test-command", "pytest"]
        )
    )
    assert args.confirm_survivors is True


def test_cli_flag_disables_confirmation():
    parser = build_parser()
    args = parser.parse_args(
        normalize_argv(
            [
                "--project-root-source-dir",
                "x",
                "--test-command",
                "pytest",
                "--no-confirm-survivors",
            ]
        )
    )
    assert args.confirm_survivors is False

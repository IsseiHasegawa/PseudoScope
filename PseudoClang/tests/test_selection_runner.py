"""Tests for selected-test command building, the bool runner, validation,
and the orchestration that turns a lookup into an execution plan."""

from __future__ import annotations

from pathlib import Path

import pytest

from pseudoclang.analysis import (
    map_selects_any_mutant,
    resolve_execution_plan,
    full_command_judges_any_mutant,
)
from pseudoclang.coverage_map import (
    JUDGMENT_FULL_ABSENT,
    JUDGMENT_FULL_NO_MAP,
    JUDGMENT_FULL_NO_TEMPLATE,
    JUDGMENT_FULL_STARTUP_ONLY,
    JUDGMENT_SELECTED,
    JUDGMENT_SKIPPED_UNCOVERED,
    CoverageMap,
    PlanKind,
)
from pseudoclang.executor import run_selected_tests, survived_without_running
from pseudoclang.models import ConfigError, PseudoScopeConfig
from pseudoclang.mutate import MutatedSource
from pseudoclang.runner import TestRunError as RunnerError
from pseudoclang.runner import (
    build_selected_command,
    run_selected_test_command,
    run_test_list_command,
)
from pseudoclang.validation import build_config, validate_selection_options


def _config(tmp_path: Path, **overrides) -> PseudoScopeConfig:
    fields = dict(
        project_root=tmp_path,
        relative_file_path=Path("src/ujson/python/objToJSON.c"),
        target_file=tmp_path / "src/ujson/python/objToJSON.c",
        function_name=None,
        test_command="pytest",
        output_path=tmp_path / "out.json",
        timeout_seconds=30,
        mode=None,
        lang=None,
    )
    fields.update(overrides)
    return PseudoScopeConfig(**fields)


def _coverage_map(tmp_path: Path) -> CoverageMap:
    return CoverageMap(
        coverage={
            "src/ujson/python/objToJSON.c": {
                "Dict_iterNext": [
                    "tests/test_ujson.py::test_dumps",
                    "tests/test_ujson.py::test_x",
                ],
                "PyInit_thing": ["(startup)"],
            },
        },
        tests=["tests/test_ujson.py::test_dumps", "tests/test_ujson.py::test_x"],
        meta={"schema": "pstrace-coverage/1", "project_root": str(tmp_path)},
    )


# -- command building (shell quoting) ---------------------------------------


def test_build_selected_command_basic():
    cmd = build_selected_command(
        "pytest {selection}", ("tests/test_a.py::test_one",)
    )
    assert cmd == "pytest tests/test_a.py::test_one"


def test_build_selected_command_quotes_parametrized_nodeids():
    nodeids = (
        "tests/test_ujson.py::test_encode[test_input1-{\"1\":1,\"0\":0}]",
        "tests/test_ujson.py::test_dump_long_string[10919-2]",
        "tests/test_ujson.py::test_separators[None-{\"a\":0, \"b\":1}]",
    )
    cmd = build_selected_command("pip install -e . && pytest {selection}", nodeids)
    # The build step is preserved and each nodeid is individually quoted.
    assert cmd.startswith("pip install -e . && pytest ")
    for nodeid in nodeids:
        import shlex

        assert shlex.quote(nodeid) in cmd


def test_build_selected_command_roundtrips_through_shlex():
    import shlex

    nodeids = (
        "tests/t.py::test[a, b]",
        "tests/t.py::test['quoted']",
        "tests/t.py::test with space",
    )
    cmd = build_selected_command("run {selection}", nodeids)
    # The shell would split the command back into exactly these tokens.
    tokens = shlex.split(cmd)
    assert tokens == ["run", *nodeids]


# -- run_selected_tests (bool semantics) ------------------------------------


def test_run_selected_tests_true_on_success(tmp_path):
    config = _config(tmp_path, test_runner_template="true {selection}")
    assert run_selected_tests(config, ("tests/t.py::a",)) is True


def test_run_selected_tests_false_on_failure(tmp_path):
    config = _config(tmp_path, test_runner_template="false {selection}")
    assert run_selected_tests(config, ("tests/t.py::a",)) is False


def test_run_selected_command_includes_nodeids_verbatim(tmp_path):
    # printf echoes each (shell-unquoted) selector on its own line.
    config = _config(tmp_path, test_runner_template="printf '%s\\n' {selection}")
    nodeids = ("tests/t.py::test[a,b]", "tests/t.py::test_two")
    result = run_selected_test_command(config, nodeids)
    assert result.exit_code == 0
    assert result.stdout.splitlines() == list(nodeids)


def test_run_selected_without_template_raises(tmp_path):
    config = _config(tmp_path, test_runner_template=None)
    with pytest.raises(RunnerError, match="no --test-runner-template"):
        run_selected_test_command(config, ("tests/t.py::a",))


# -- survived_without_running ------------------------------------------------


def test_survived_without_running_marks_all_survived():
    mutations = [
        MutatedSource(
            path=Path("/x/y.c"),
            relative_path=Path("y.c"),
            function_name="f",
            original_content="orig",
            mutated_content="mut",
            original_body="{ return 1; }",
            replacement_body="return 0;",
            mutation_type="default_return",
            return_type_category="int",
            body_start_index=0,
            body_end_index=1,
        )
    ]
    results = survived_without_running(mutations)
    assert len(results) == 1
    assert results[0].status == "survived"
    assert results[0].restored is True
    assert results[0].exit_code is None


# -- validation -------------------------------------------------------------


def test_assume_complete_requires_map():
    from pseudoclang.models import ConfigError

    with pytest.raises(ConfigError, match="requires --coverage-map"):
        validate_selection_options(
            coverage_map_path=None,
            assume_coverage_complete=True,
            test_runner_template=None,
        )


def test_template_requires_selection_placeholder():
    from pseudoclang.models import ConfigError

    with pytest.raises(ConfigError, match="selection"):
        validate_selection_options(
            coverage_map_path=Path("/x.json"),
            assume_coverage_complete=False,
            test_runner_template="pytest -q",
        )


def test_template_with_placeholder_is_returned_stripped():
    out = validate_selection_options(
        coverage_map_path=Path("/x.json"),
        assume_coverage_complete=False,
        test_runner_template="  pytest {selection}  ",
    )
    assert out == "pytest {selection}"


# -- resolve_execution_plan (orchestration) ---------------------------------


def test_plan_no_map_is_full_no_map(tmp_path):
    config = _config(tmp_path)
    plan = resolve_execution_plan(config, None, "Dict_iterNext")
    assert plan.kind is PlanKind.RUN_FULL
    assert plan.judgment == JUDGMENT_FULL_NO_MAP


def test_plan_selected_with_template(tmp_path):
    config = _config(tmp_path, test_runner_template="pytest {selection}")
    plan = resolve_execution_plan(config, _coverage_map(tmp_path), "Dict_iterNext")
    assert plan.kind is PlanKind.RUN_SELECTED
    assert plan.judgment == JUDGMENT_SELECTED
    assert plan.nodeids == (
        "tests/test_ujson.py::test_dumps",
        "tests/test_ujson.py::test_x",
    )


def test_plan_selected_degrades_without_template(tmp_path):
    config = _config(tmp_path, test_runner_template=None)
    plan = resolve_execution_plan(config, _coverage_map(tmp_path), "Dict_iterNext")
    assert plan.kind is PlanKind.RUN_FULL
    assert plan.judgment == JUDGMENT_FULL_NO_TEMPLATE


def test_plan_startup_only_full(tmp_path):
    config = _config(tmp_path, test_runner_template="pytest {selection}")
    plan = resolve_execution_plan(config, _coverage_map(tmp_path), "PyInit_thing")
    assert plan.kind is PlanKind.RUN_FULL
    assert plan.judgment == JUDGMENT_FULL_STARTUP_ONLY


def test_plan_absent_full_by_default(tmp_path):
    config = _config(tmp_path, test_runner_template="pytest {selection}")
    plan = resolve_execution_plan(config, _coverage_map(tmp_path), "missing_fn")
    assert plan.kind is PlanKind.RUN_FULL
    assert plan.judgment == JUDGMENT_FULL_ABSENT


def test_plan_absent_skipped_when_complete(tmp_path):
    config = _config(
        tmp_path,
        test_runner_template="pytest {selection}",
        assume_coverage_complete=True,
    )
    plan = resolve_execution_plan(config, _coverage_map(tmp_path), "missing_fn")
    assert plan.kind is PlanKind.SKIP_AS_SURVIVED
    assert plan.judgment == JUDGMENT_SKIPPED_UNCOVERED


def test_plan_warns_on_stale_universe(tmp_path, capsys):
    cov = CoverageMap(
        coverage={
            "src/ujson/python/objToJSON.c": {
                "f": ["tests/test_ujson.py::ghost"],  # not in tests universe
            }
        },
        tests=["tests/test_ujson.py::real"],
        meta={"schema": "pstrace-coverage/1", "project_root": str(tmp_path)},
    )
    config = _config(tmp_path, test_runner_template="pytest {selection}")
    plan = resolve_execution_plan(config, cov, "f")
    assert plan.kind is PlanKind.RUN_SELECTED
    err = capsys.readouterr().err
    # The warning names an intra-map inconsistency, not live-suite staleness.
    assert "absent from the map's own 'tests' universe" in err
    assert "internally inconsistent" in err


# -- preflight gating predicates --------------------------------------------


def test_test_command_judges_when_no_map(tmp_path):
    cfg = _config(tmp_path)
    assert full_command_judges_any_mutant(cfg, None, ["Dict_iterNext"]) is True


def test_test_command_judges_when_no_template(tmp_path):
    cfg = _config(tmp_path)  # coverage map but no template -> RUN_FULL
    assert (
        full_command_judges_any_mutant(cfg, _coverage_map(tmp_path), ["Dict_iterNext"])
        is True
    )


def test_test_command_not_judged_when_all_selected_no_confirm(tmp_path):
    # Without confirmation, a pure-selected run judges every mutant via the
    # template, so the full --test-command judges nothing (and its rebuild probe
    # is skipped).
    cfg = _config(
        tmp_path,
        test_runner_template="pytest {selection}",
        confirm_survivors=False,
    )
    assert (
        full_command_judges_any_mutant(cfg, _coverage_map(tmp_path), ["Dict_iterNext"])
        is False
    )


def test_test_command_judged_when_confirming_selected(tmp_path):
    # With confirmation on (the default), a selected survivor is re-judged against
    # the full --test-command, so it must be rebuild-verified even in a pure-selected
    # run: the predicate must report the full command judges a mutant.
    cfg = _config(
        tmp_path,
        test_runner_template="pytest {selection}",
        confirm_survivors=True,
    )
    assert (
        full_command_judges_any_mutant(cfg, _coverage_map(tmp_path), ["Dict_iterNext"])
        is True
    )


def test_test_command_judged_on_absent_fallback(tmp_path):
    cfg = _config(tmp_path, test_runner_template="pytest {selection}")
    assert (
        full_command_judges_any_mutant(cfg, _coverage_map(tmp_path), ["ghost_fn"])
        is True
    )


def test_test_command_judged_on_startup_only(tmp_path):
    cfg = _config(tmp_path, test_runner_template="pytest {selection}")
    assert (
        full_command_judges_any_mutant(cfg, _coverage_map(tmp_path), ["PyInit_thing"])
        is True
    )


def test_test_command_judged_when_any_function_runs_full(tmp_path):
    cfg = _config(tmp_path, test_runner_template="pytest {selection}")
    assert (
        full_command_judges_any_mutant(
            cfg, _coverage_map(tmp_path), ["Dict_iterNext", "ghost_fn"]
        )
        is True
    )


def test_map_selects_none_without_map(tmp_path):
    cfg = _config(tmp_path, test_runner_template="pytest {selection}")
    assert map_selects_any_mutant(cfg, None, ["Dict_iterNext"]) is False


def test_map_selects_none_without_template(tmp_path):
    cfg = _config(tmp_path)
    assert (
        map_selects_any_mutant(cfg, _coverage_map(tmp_path), ["Dict_iterNext"]) is False
    )


def test_map_selects_true_for_selected(tmp_path):
    cfg = _config(tmp_path, test_runner_template="pytest {selection}")
    assert (
        map_selects_any_mutant(cfg, _coverage_map(tmp_path), ["Dict_iterNext"]) is True
    )


def test_map_selects_false_for_absent(tmp_path):
    cfg = _config(tmp_path, test_runner_template="pytest {selection}")
    assert map_selects_any_mutant(cfg, _coverage_map(tmp_path), ["ghost_fn"]) is False


def test_map_selects_false_for_startup_only(tmp_path):
    cfg = _config(tmp_path, test_runner_template="pytest {selection}")
    assert (
        map_selects_any_mutant(cfg, _coverage_map(tmp_path), ["PyInit_thing"]) is False
    )


# -- run_test_list_command --------------------------------------------------


def test_run_test_list_command_returns_lines(tmp_path):
    cfg = _config(tmp_path, test_list_cmd=r"printf 'x::a\nx::b\n'")
    result = run_test_list_command(cfg)
    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["x::a", "x::b"]


def test_run_test_list_command_requires_cmd(tmp_path):
    cfg = _config(tmp_path)  # no --test-list-cmd
    with pytest.raises(RunnerError):
        run_test_list_command(cfg)


# -- validation: --test-list-cmd requires --coverage-map --------------------


def test_test_list_cmd_requires_coverage_map(tmp_path):
    with pytest.raises(ConfigError):
        build_config(
            project_root_source_dir=str(tmp_path),
            file=None,
            function=None,
            test_command="pytest",
            output_dir=None,
            output_file=None,
            timeout=30,
            mode=None,
            lang=None,
            test_list_cmd="pytest --collect-only",
        )


def test_test_list_cmd_ok_with_coverage_map(tmp_path):
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
        coverage_map="cov.json",
        test_list_cmd="pytest --collect-only",
    )
    assert cfg.test_list_cmd == "pytest --collect-only"
    assert cfg.coverage_map_path is not None

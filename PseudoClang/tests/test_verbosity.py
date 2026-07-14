"""-v / -vv / -q verbosity: gating helpers, per-mutant detail, and CLI flags.

A single integer ``config.verbosity`` drives all ordinary output. These lock in
that ``-q`` silences narration, ``-v`` adds per-mutant exit/runtime, and ``-vv``
adds the exact command plus a captured-output tail.
"""

from __future__ import annotations

from types import SimpleNamespace

from pseudoclang import reporting
from pseudoclang.cli import build_parser
from pseudoclang.executor import MutationRunResult
from pseudoclang.results import mutant_detail_lines


def _cfg(verbosity: int) -> SimpleNamespace:
    return SimpleNamespace(verbosity=verbosity)


def _result(**overrides) -> MutationRunResult:
    base = dict(
        function_name="foo",
        mutation_type="default_return",
        return_type_category="int",
        replacement_body="return 0;",
        exit_code=0,
        timed_out=False,
        stdout="",
        stderr="",
        runtime_seconds=1.5,
        status="survived",
        restored=True,
        test_command="pytest -q",
    )
    base.update(overrides)
    return MutationRunResult(**base)


# --- reporting gating -------------------------------------------------------

def test_chatty_prints_at_normal_but_not_quiet(capsys):
    reporting.chatty(_cfg(0), "hi")
    assert capsys.readouterr().out == ""
    reporting.chatty(_cfg(1), "hi")
    assert capsys.readouterr().out == "hi\n"


def test_detail_only_at_verbose(capsys):
    reporting.detail(_cfg(1), "d")
    assert capsys.readouterr().out == ""
    reporting.detail(_cfg(2), "d")
    assert capsys.readouterr().out == "d\n"


def test_trace_only_at_trace(capsys):
    reporting.trace(_cfg(2), "t")
    assert capsys.readouterr().out == ""
    reporting.trace(_cfg(3), "t")
    assert capsys.readouterr().out == "t\n"


def test_tail_lines_clips_and_skips_blank():
    assert reporting.tail_lines("", 5) == ([], 0)
    assert reporting.tail_lines("   \n\n", 5) == ([], 0)
    shown, total = reporting.tail_lines("a\nb\nc\nd", 2)
    assert shown == ["c", "d"] and total == 4


# --- per-mutant detail formatter --------------------------------------------

def test_no_detail_below_verbose():
    assert mutant_detail_lines(_result(), level=1) == []


def test_verbose_adds_exit_and_runtime_line():
    lines = mutant_detail_lines(_result(exit_code=0, runtime_seconds=2.0), level=2)
    assert len(lines) == 1
    assert "exit 0" in lines[0] and "2.00s" in lines[0]


def test_verbose_shows_timeout_marker():
    lines = mutant_detail_lines(_result(timed_out=True, status="timeout"), level=2)
    assert "exit timeout" in lines[0]


def test_trace_adds_command_and_output_tail():
    lines = mutant_detail_lines(
        _result(stdout="l1\nl2\nl3", stderr="boom"), level=3, tail=2
    )
    joined = "\n".join(lines)
    assert "$ pytest -q" in joined
    assert "l2" in joined and "l3" in joined  # last 2 of stdout
    assert "l1" not in joined                 # clipped
    assert "boom" in joined                   # stderr shown
    assert "(last 2 of 3)" in joined          # clip annotation


def test_trace_skips_empty_streams():
    lines = mutant_detail_lines(_result(stdout="", stderr=""), level=3)
    assert not any("stdout" in ln or "stderr" in ln for ln in lines)


# --- CLI flags --------------------------------------------------------------

def _parse(argv):
    return build_parser().parse_args(argv)


def test_run_and_analyze_expose_verbosity_flags():
    parser = build_parser()
    sub = next(
        a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
    )
    for command in ("run", "analyze"):
        opts = {o for action in sub.choices[command]._actions for o in action.option_strings}
        assert {"-v", "--verbose", "-q", "--quiet"} <= opts, command


def test_verbose_is_countable():
    args = _parse(["run", "--project-root-source-dir", "x", "--test-command", "t", "-vv"])
    assert args.verbose == 2
    assert args.quiet is False


def test_quiet_flag_parses():
    args = _parse(["run", "--project-root-source-dir", "x", "--test-command", "t", "-q"])
    assert args.quiet is True

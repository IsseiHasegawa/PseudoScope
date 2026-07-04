"""Stage sub-commands: run (default) / coverage-map / analyze.

Locks in Phase A: the CLI splits into standalone stages so an improved test
suite can be re-checked with ``analyze`` without rebuilding the map, while the
old sub-command-less invocation keeps working via an implicit ``run``.
"""

from __future__ import annotations

from pseudoclang.cli import build_parser, main, normalize_argv


def _options(subcommand: str) -> set[str]:
    """The option strings actually accepted by one sub-command's parser."""
    parser = build_parser()
    sub_action = next(
        a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
    )
    sub = sub_action.choices[subcommand]
    opts: set[str] = set()
    for action in sub._actions:
        opts.update(action.option_strings)
    return opts


# --- implicit-run normalization (backward compatibility) --------------------

def test_normalize_injects_run_for_bare_flags():
    assert normalize_argv(["--project-root-source-dir", "x"]) == [
        "run", "--project-root-source-dir", "x"
    ]


def test_normalize_injects_run_for_empty():
    assert normalize_argv([]) == ["run"]


def test_normalize_leaves_known_subcommands_alone():
    for cmd in ("run", "coverage-map", "analyze"):
        assert normalize_argv([cmd, "--foo"]) == [cmd, "--foo"]


def test_normalize_leaves_top_level_help_alone():
    assert normalize_argv(["--help"]) == ["--help"]
    assert normalize_argv(["-h"]) == ["-h"]


# --- flag partitioning per stage --------------------------------------------

def test_run_has_the_full_flag_set():
    opts = _options("run")
    for flag in (
        "--test-command", "--file", "--function", "--coverage-map",
        "--coverage-map-cmd", "--pstrace-module", "--refresh-coverage-map",
        "--assume-coverage-complete",
    ):
        assert flag in opts, flag


def test_coverage_map_stage_omits_analysis_flags():
    opts = _options("coverage-map")
    # It generates the map: no mutation test command or target file needed.
    assert "--test-command" not in opts
    assert "--file" not in opts
    assert "--function" not in opts
    # It does take the generator flags.
    assert "--coverage-map" in opts
    assert "--coverage-map-cmd" in opts
    assert "--pstrace-module" in opts


def test_analyze_stage_omits_generation_flags():
    opts = _options("analyze")
    # It reuses an existing map and must never regenerate it.
    assert "--coverage-map-cmd" not in opts
    assert "--refresh-coverage-map" not in opts
    assert "--pstrace-module" not in opts
    # It still consumes a map and analyzes a target.
    assert "--coverage-map" in opts
    assert "--file" in opts
    assert "--test-command" in opts


# --- coverage-map stage behavior --------------------------------------------

def test_coverage_map_requires_a_generator(tmp_path):
    rc = main(["coverage-map", "--project-root-source-dir", str(tmp_path)])
    assert rc == 1  # no --pstrace-module and no --coverage-map-cmd


def test_coverage_map_generates_then_reuses_then_refreshes(tmp_path):
    map_path = tmp_path / "cov.json"

    def run(cmd_body: str, *extra: str) -> int:
        return main([
            "coverage-map",
            "--project-root-source-dir", str(tmp_path),
            "--coverage-map", str(map_path),
            "--coverage-map-cmd", cmd_body,
            *extra,
        ])

    # First call generates.
    assert run('printf "one" > "$PSEUDOCLANG_COVERAGE_MAP"') == 0
    assert map_path.read_text() == "one"

    # Second call reuses the existing file (does NOT run the command again).
    assert run('printf "two" > "$PSEUDOCLANG_COVERAGE_MAP"') == 0
    assert map_path.read_text() == "one"

    # With --refresh-coverage-map it regenerates.
    assert run('printf "three" > "$PSEUDOCLANG_COVERAGE_MAP"', "--refresh-coverage-map") == 0
    assert map_path.read_text() == "three"


def test_coverage_map_stage_does_not_require_test_command(tmp_path):
    # --test-command is intentionally absent from this stage; generation works
    # without it.
    map_path = tmp_path / "cov.json"
    rc = main([
        "coverage-map",
        "--project-root-source-dir", str(tmp_path),
        "--coverage-map", str(map_path),
        "--coverage-map-cmd", 'printf "x" > "$PSEUDOCLANG_COVERAGE_MAP"',
    ])
    assert rc == 0
    assert map_path.exists()

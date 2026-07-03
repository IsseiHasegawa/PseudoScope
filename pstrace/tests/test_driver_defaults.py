"""The default coverage-json location lives under pstrace, not the target.

A run with no explicit ``--coverage-json`` must not write into the traced
project's tree, so the target stays original.
"""

from __future__ import annotations

from pstrace import driver


def test_default_output_dir_is_under_pstrace_repo():
    out = driver._DEFAULT_OUTPUT_DIR
    assert out.name == "output"
    # Sibling of the pstrace package (repo root), i.e. the tool's own tree.
    assert (out.parent / "pstrace" / "driver.py").is_file()


def test_default_coverage_json_sits_in_that_dir():
    default = (driver._DEFAULT_OUTPUT_DIR / "coverage.json").resolve()
    assert default.parent == driver._DEFAULT_OUTPUT_DIR.resolve()
    assert default.name == "coverage.json"

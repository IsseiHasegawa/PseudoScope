"""Default output locations resolve under PseudoClang, not the target project.

Keeps a run from leaving artifacts in the target tree, so it stays original.
"""

from __future__ import annotations

from pathlib import Path

from pseudoclang.validation import default_output_dir, resolve_output_path


def test_default_output_dir_is_under_pseudoclang_repo():
    out = default_output_dir()
    assert out.name == "output"
    # Sibling of the pseudoclang package (repo root), i.e. the tool's own tree.
    assert (out.parent / "pseudoclang").is_dir()


def test_output_defaults_under_tool_not_target(tmp_path):
    path = resolve_output_path(output_dir=None, output_file=None, project_root=tmp_path)
    assert path == default_output_dir() / "pseudoclang-results.json"
    # Never inside the target project.
    assert tmp_path not in path.parents


def test_blank_output_dir_falls_back_to_default(tmp_path):
    path = resolve_output_path(output_dir="   ", output_file=None, project_root=tmp_path)
    assert path.parent == default_output_dir()


def test_explicit_relative_output_dir_still_resolves_under_target(tmp_path):
    path = resolve_output_path(
        output_dir="results", output_file="r.json", project_root=tmp_path
    )
    assert path == (tmp_path / "results" / "r.json").resolve()


def test_explicit_absolute_output_dir_is_honored(tmp_path):
    other = tmp_path / "elsewhere"
    path = resolve_output_path(
        output_dir=str(other), output_file="r.json", project_root=tmp_path
    )
    assert path == (other / "r.json").resolve()


def test_custom_output_file_name_kept(tmp_path):
    path = resolve_output_path(
        output_dir=None, output_file="custom.json", project_root=tmp_path
    )
    assert path == default_output_dir() / "custom.json"

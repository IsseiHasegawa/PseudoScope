"""
Build a pstrace coverage-map generation command from ``--pstrace-*`` flags.

Lets PseudoClang drive pstrace itself: instead of hand-writing the full
``pstrace.driver`` invocation in ``--coverage-map-cmd`` (or running pstrace
separately), the user passes a few ``--pstrace-*`` flags and PseudoClang
constructs the command that generates the map at ``$PSEUDOCLANG_COVERAGE_MAP``.

pstrace is expected as a sibling checkout of the PseudoClang repo (the directory
that contains the ``pstrace`` package), i.e. ``<repo>/../pstrace`` by default;
override with ``--pstrace-repo``.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from pseudoclang.models import ConfigError


def default_pstrace_repo() -> Path:
    """The sibling pstrace checkout: ``<this-repo>/../pstrace``.

    This file lives at ``<repo>/pseudoclang/pstrace_integration.py``; parents[2]
    is the directory that holds both the PseudoClang repo and ``pstrace``.
    """
    return Path(__file__).resolve().parents[2] / "pstrace"


def _pstrace_package_present(repo: Path) -> bool:
    return (repo / "pstrace" / "driver.py").is_file()


def build_pstrace_coverage_map_cmd(
    *,
    project_root: Path,
    module: str,
    src_root: str,
    build_cmd: str,
    test_cmd: str,
    python: str | None = None,
    repo: str | None = None,
    instrument_paths: tuple[str, ...] | list[str] = (),
    hook_in: str | None = None,
    hook_mode: str | None = None,
) -> str:
    """Return a shell command that runs ``pstrace.driver`` for this project.

    The command writes the map to ``$PSEUDOCLANG_COVERAGE_MAP`` (which PseudoClang
    sets), so it can be used directly as ``config.coverage_map_cmd``. Raises
    :class:`ConfigError` if the pstrace checkout cannot be found.
    """
    repo_path = (
        Path(repo).expanduser().resolve() if repo else default_pstrace_repo()
    )
    if not _pstrace_package_present(repo_path):
        raise ConfigError(
            f"--pstrace-module needs the pstrace package, not found under "
            f"{repo_path}. Clone pstrace next to PseudoClang, or pass "
            "--pstrace-repo <path-to-pstrace-checkout>."
        )

    interpreter = python or sys.executable
    src = Path(src_root)
    src_abs = src if src.is_absolute() else (Path(project_root) / src).resolve()

    parts: list[str] = [
        f"PYTHONPATH={shlex.quote(str(repo_path))}",
        shlex.quote(interpreter),
        "-m",
        "pstrace.driver",
        "--project-root",
        shlex.quote(str(project_root)),
        "--python",
        shlex.quote(interpreter),
        "--module",
        shlex.quote(module),
        "--src-root",
        shlex.quote(str(src_abs)),
        "--build-cmd",
        shlex.quote(build_cmd),
        "--test-cmd",
        shlex.quote(test_cmd),
    ]
    for path in instrument_paths:
        parts += ["--instrument-path", shlex.quote(path)]
    if hook_in:
        parts += ["--hook-in", shlex.quote(hook_in)]
    if hook_mode:
        parts += ["--hook-mode", shlex.quote(hook_mode)]
    # NOT quoted: the shell must expand the variable PseudoClang sets.
    parts += ["--coverage-json", '"$PSEUDOCLANG_COVERAGE_MAP"']
    return " ".join(parts)

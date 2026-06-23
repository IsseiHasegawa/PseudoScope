"""Build a target's C/C++ extension with pstrace instrumentation, WITHOUT
editing the target's setup.py.

It monkeypatches ``setuptools`` so that, while the target's own ``setup.py``
runs, the chosen extension(s) get the trace hook added as a source and
``-finstrument-functions`` appended to their compile args. Then it executes the
target's ``setup.py`` in-process.

Usage (run from the target project, with this package importable)::

    # single-extension target (ujson)
    python -m pstrace.build --target ujson \
        --include ../count-function-call/include \
        -- setup.py build_ext --inplace --force

    # multi-extension target: instrument just one (Pillow)
    python -m pstrace.build --target PIL._imaging \
        -- setup.py build_clib build_ext --inplace

Everything before ``--`` configures pstrace; everything after ``--`` is the
target's own ``setup.py`` command line, passed through untouched.
"""

from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path

# Default instrumentation flags. -O0 keeps functions from being inlined away so
# every one is observable; -g/no-strip lets the symbolizer recover file:line.
DEFAULT_FLAGS = [
    "-finstrument-functions",
    "-O0",
    "-g",
    "-fno-omit-frame-pointer",
]


def _repo_root() -> Path:
    # this file is <repo>/pstrace/build.py -> repo root is two parents up
    return Path(__file__).resolve().parent.parent


def _install_patch(
    targets: set[str],
    hook_source: str,
    include_dirs: list[str],
    flags: list[str],
) -> None:
    """Patch build_ext.build_extensions to inject pstrace into matching exts."""
    from setuptools.command.build_ext import build_ext

    original = build_ext.build_extensions

    def patched(self: build_ext) -> None:
        for ext in self.extensions:
            if targets and ext.name not in targets:
                continue
            if hook_source not in ext.sources:
                ext.sources.append(hook_source)
            for inc in include_dirs:
                if inc not in ext.include_dirs:
                    ext.include_dirs.append(inc)
            ext.extra_compile_args = list(ext.extra_compile_args or []) + flags
            print(f"pstrace: instrumenting extension {ext.name!r}", file=sys.stderr)
        return original(self)

    build_ext.build_extensions = patched  # type: ignore[method-assign]


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--" in raw:
        sep = raw.index("--")
        our_args, setup_argv = raw[:sep], raw[sep + 1 :]
    else:
        our_args, setup_argv = raw, []

    ap = argparse.ArgumentParser(prog="pstrace.build", description=__doc__)
    ap.add_argument(
        "--target",
        action="append",
        default=[],
        metavar="EXT_NAME",
        help="extension name to instrument (repeatable). Omit to instrument all.",
    )
    ap.add_argument(
        "--repo",
        default=str(_repo_root()),
        help="pstrace repo root (default: this package's own location)",
    )
    ap.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="DIR",
        help="extra include dir to add to the instrumented extension (repeatable)",
    )
    ap.add_argument(
        "--flag",
        action="append",
        default=[],
        metavar="CFLAG",
        help="extra compile flag to append (repeatable)",
    )
    args = ap.parse_args(our_args)

    if not setup_argv:
        ap.error("missing target command after '--', e.g. -- setup.py build_ext --inplace")

    repo = Path(args.repo).resolve()
    hook_source = str(repo / "src" / "pstrace_hook.c")
    if not Path(hook_source).is_file():
        ap.error(f"hook source not found: {hook_source}")

    include_dirs = [str(repo / "include")] + [os.path.abspath(d) for d in args.include]
    flags = DEFAULT_FLAGS + args.flag

    _install_patch(set(args.target), hook_source, include_dirs, flags)

    setup_script = setup_argv[0]
    if not Path(setup_script).is_file():
        ap.error(f"setup script not found in cwd: {setup_script}")

    # Run the target's setup.py in-process so the monkeypatch is active.
    sys.argv = list(setup_argv)
    try:
        runpy.run_path(setup_script, run_name="__main__")
    except SystemExit as exc:  # setuptools raises SystemExit on failure
        code = exc.code
        if isinstance(code, int):
            return code
        if code is None:
            return 0
        print(code, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Generic, build-system-agnostic pstrace driver (successor to the ujson recipe).

Given a project's own *build* and *test* commands, this:

  1. compiles the pstrace hook to an object once,
  2. runs the build command with ``CC`` / ``CXX`` (and ``LDSHARED`` for
     setuptools) pointed at the pstrace compiler wrapper, so the target's
     C/C++ is instrumented and the hook is linked into its extension(s),
  3. runs the test command under pytest with the tracing plugin loaded, and
  4. turns the raw trace into a ``pstrace-coverage/1`` map for PseudoClang.

It makes no assumptions about the build system: setuptools, Meson, CMake and
autotools all honour ``CC`` / ``CXX``. Point PseudoClang's ``--coverage-map-cmd``
at an invocation of this module.

setuptools (inplace build; the extension is importable from the project cwd)::

    python -m pstrace.driver \
        --project-root ultrajson --python ultrajson/.venv/bin/python \
        --module ujson --src-root ultrajson/src/ujson \
        --build-cmd "python setup.py build_ext --inplace --force" \
        --test-cmd  "python -m pytest tests/" \
        --coverage-json coverage.json

Meson / meson-python (large, multi-extension). Use an *editable* install so the
object files survive for symbolization (on macOS DWARF lives in the ``.o`` files;
a non-editable ``pip install .`` deletes its ephemeral build dir and ``atos`` can
no longer recover file:line). ``--instrument-path`` limits -O0 instrumentation to
one subtree and ``--hook-in`` keeps a single hook instance; ``--test-dir`` runs
the tests from a neutral cwd so the source tree does not shadow the installed
package::

    python -m pstrace.driver \
        --project-root numpy --python .venv/bin/python \
        --module numpy._core._multiarray_umath \
        --src-root numpy/numpy/_core/src/multiarray \
        --instrument-path src/multiarray --hook-in _multiarray_umath \
        --build-cmd "pip install -e . --no-build-isolation -Csetup-args=-Dallow-noblas=true" \
        --test-cmd  "python -m pytest --pyargs numpy._core.tests.test_multiarray" \
        --test-dir /tmp --coverage-json coverage.json
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent  # <repo>/pstrace/pstrace
_REPO = _PKG_DIR.parent  # <repo>/pstrace (the dir that contains the package)
_HOOK_SRC = _REPO / "src" / "pstrace_hook.c"
_INCLUDE = _REPO / "include"
_SHIM_CC = _PKG_DIR / "pstrace-cc"
_SHIM_CXX = _PKG_DIR / "pstrace-cxx"


class DriverError(Exception):
    """A step (build, test, report) failed."""


def _target_config_vars(python: str) -> dict[str, str]:
    """Read CC/CXX/LDSHARED/LDCXXSHARED from the target interpreter's sysconfig."""
    code = (
        "import sysconfig, json;"
        "print(json.dumps({k: (sysconfig.get_config_var(k) or '') "
        "for k in ['CC', 'CXX', 'LDSHARED', 'LDCXXSHARED']}))"
    )
    try:
        out = subprocess.check_output([python, "-c", code], text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DriverError(f"cannot read sysconfig from {python!r}: {exc}") from exc
    return json.loads(out)


def _swap_launcher(config_value: str, shim: Path) -> str:
    """Replace the compiler token in an ``LDSHARED``-style string with ``shim``.

    ``"clang -bundle -undefined dynamic_lookup"`` -> ``"<shim> -bundle ..."``.
    The wrapper re-adds the real compiler from ``PSTRACE_REAL_CC``.
    """
    parts = config_value.split()
    if not parts:
        return str(shim)
    return " ".join([str(shim)] + parts[1:])


def _compile_hook(real_cc: str, work: Path) -> Path:
    hook_obj = work / "pstrace_hook.o"
    cmd = [
        *shlex.split(real_cc),
        "-c",
        "-fPIC",
        "-O2",
        "-pthread",
        "-I",
        str(_INCLUDE),
        str(_HOOK_SRC),
        "-o",
        str(hook_obj),
    ]
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise DriverError(f"failed to compile the pstrace hook: {' '.join(cmd)}")
    return hook_obj


def _build_hook_lib(real_cc: str, work: Path) -> Path:
    """Build the hook as a standalone shared library for the preload path.

    Linux resolves an instrumented ``.so``'s ``__cyg_profile_func_enter`` PLT
    call through the global scope; a hook linked *into* the extension (loaded
    ``RTLD_LOCAL`` by CPython) is never reached. Preloading a single
    ``libpstrace`` puts the hook in the global scope so every instrumented
    extension shares it (this also sidesteps the multi-`.so` state split).
    """
    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    lib = work / f"libpstrace{suffix}"
    cmd = [
        *shlex.split(real_cc),
        "-shared", "-fPIC", "-O2", "-pthread",
        "-I", str(_INCLUDE), str(_HOOK_SRC),
        "-o", str(lib),
    ]
    if sys.platform.startswith("linux"):
        cmd.append("-ldl")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise DriverError(f"failed to build the pstrace hook library: {' '.join(cmd)}")
    return lib


def _run(command: str, *, cwd: Path, env: dict[str, str], label: str) -> None:
    print(f"pstrace-driver: [{label}] $ {command}", file=sys.stderr)
    proc = subprocess.run(command, cwd=str(cwd), env=env, shell=True)
    if proc.returncode != 0:
        raise DriverError(f"{label} command failed (exit {proc.returncode}): {command}")


def _prepend_pythonpath(env: dict[str, str], *paths: str) -> None:
    existing = env.get("PYTHONPATH", "")
    parts = [p for p in paths if p]
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)


def _prepend_path(env: dict[str, str], directory: str) -> None:
    """Put ``directory`` first on PATH so bare ``python`` / ``pytest`` in the
    user's commands resolve to the target interpreter (venv-activation style)."""
    existing = env.get("PATH", "")
    env["PATH"] = directory + (os.pathsep + existing if existing else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pstrace.driver", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project-root", required=True,
                    help="target project root (cwd for build and test)")
    ap.add_argument("--build-cmd", required=True,
                    help="shell command that performs a clean, forced rebuild")
    ap.add_argument("--test-cmd", required=True,
                    help="shell command that runs the pytest suite")
    ap.add_argument("--coverage-json", required=True,
                    help="output path for the pstrace-coverage/1 map")
    ap.add_argument("--src-root", required=True,
                    help="keep only functions defined under this source tree")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--module", help="importable module whose .so exports the hook")
    group.add_argument("--lib", help="explicit path to the instrumented .so")
    ap.add_argument("--python", default=sys.executable,
                    help="target interpreter (default: the driver's own)")
    ap.add_argument("--real-cc", help="real C compiler (default: target sysconfig CC)")
    ap.add_argument("--real-cxx", help="real C++ compiler (default: target sysconfig CXX)")
    ap.add_argument("--include", action="append", default=[],
                    help="extra include dir for instrumented sources (repeatable)")
    ap.add_argument("--flag", action="append", default=[],
                    help="extra compile flag appended verbatim (repeatable)")
    ap.add_argument("--instrument-path", action="append", default=[],
                    help="only instrument sources whose path contains this "
                         "substring (repeatable; default: instrument everything). "
                         "Use in large projects to limit -O0 instrumentation and "
                         "keep instrumented code in one extension.")
    ap.add_argument("--hook-in",
                    help="add the hook only to the shared library whose output "
                         "name contains this substring. Keeps a single hook "
                         "instance in a multi-extension project (default: every "
                         "shared library). Ignored in preload mode.")
    ap.add_argument("--hook-mode", choices=["auto", "link", "preload"], default="auto",
                    help="how the hook reaches the extension. 'link' injects the "
                         "hook object into the .so (works on macOS). 'preload' "
                         "loads a shared libpstrace via LD_PRELOAD / "
                         "DYLD_INSERT_LIBRARIES (required on Linux, where a linked "
                         "hook is never reached, and it also handles "
                         "multi-extension projects). 'auto' = preload on Linux, "
                         "link elsewhere.")
    ap.add_argument("--test-dir",
                    help="working directory for the test command (default: the "
                         "project root). Set to a neutral directory when the "
                         "extension is installed rather than built inplace (e.g. "
                         "meson-python), so the source tree does not shadow the "
                         "installed package via the test's cwd.")
    ap.add_argument("--keep-file", action="append", default=[],
                    help="extra source basename to keep in the coverage map "
                         "(repeatable); forwarded to the report. Useful for "
                         "Cython-generated .c files that live outside --src-root.")
    ap.add_argument("--work-dir", help="keep artifacts here (default: a temp dir)")
    args = ap.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        ap.error(f"--project-root is not a directory: {project_root}")
    test_cwd = Path(args.test_dir).resolve() if args.test_dir else project_root
    if not test_cwd.is_dir():
        ap.error(f"--test-dir is not a directory: {test_cwd}")
    if not _HOOK_SRC.is_file():
        ap.error(f"hook source missing: {_HOOK_SRC}")

    work = Path(args.work_dir).resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix="pstrace-"))
    work.mkdir(parents=True, exist_ok=True)

    # Do not resolve symlinks: a venv's ``bin/python`` symlinks to the base
    # interpreter, and we want the venv's own bin dir on PATH so bare ``python``
    # / ``pytest`` in the user's commands hit the venv, not the base install.
    py_bin_dir = os.path.dirname(os.path.abspath(args.python))

    cfg = _target_config_vars(args.python)
    real_cc = args.real_cc or (cfg["CC"].split()[0] if cfg["CC"] else "cc")
    real_cxx = args.real_cxx or (cfg["CXX"].split()[0] if cfg["CXX"] else "c++")

    use_preload = args.hook_mode == "preload" or (
        args.hook_mode == "auto" and sys.platform.startswith("linux")
    )
    if use_preload and sys.platform == "darwin":
        print("pstrace-driver: warning: preload on macOS relies on "
              "DYLD_INSERT_LIBRARIES, which System Integrity Protection strips when "
              "the shell-run test command execs a protected binary, so the hook may "
              "never load. Use --hook-mode link on macOS (single extension).",
              file=sys.stderr)

    try:
        hook_lib = _build_hook_lib(real_cc, work) if use_preload else None
        hook_obj = None if use_preload else _compile_hook(real_cc, work)
    except DriverError as exc:
        print(f"pstrace-driver: {exc}", file=sys.stderr)
        return 1

    include_dirs = os.pathsep.join([str(_INCLUDE), *[os.path.abspath(d) for d in args.include]])

    # --- build environment: point the toolchain at the wrapper -----------------
    build_env = dict(os.environ)
    build_env.update(
        PSTRACE_REAL_CC=real_cc,
        PSTRACE_REAL_CXX=real_cxx,
        PSTRACE_INCLUDE=include_dirs,
        CC=str(_SHIM_CC),
        CXX=str(_SHIM_CXX),
    )
    # Link mode injects the hook object into the .so; preload mode leaves
    # __cyg_profile_func_enter undefined in the .so (resolved at load from the
    # preloaded libpstrace), so the hook object is not passed to the wrapper.
    if not use_preload:
        build_env["PSTRACE_HOOK_OBJ"] = str(hook_obj)
    if args.flag:
        build_env["PSTRACE_EXTRA_FLAGS"] = " ".join(args.flag)
    if args.instrument_path:
        build_env["PSTRACE_TARGET"] = os.pathsep.join(args.instrument_path)
    if args.hook_in:
        build_env["PSTRACE_HOOK_LINK_MATCH"] = args.hook_in
    # setuptools links via LDSHARED, not CC; Meson/CMake ignore these harmlessly.
    if cfg["LDSHARED"]:
        build_env["LDSHARED"] = _swap_launcher(cfg["LDSHARED"], _SHIM_CC)
    if cfg["LDCXXSHARED"]:
        build_env["LDCXXSHARED"] = _swap_launcher(cfg["LDCXXSHARED"], _SHIM_CXX)
    _prepend_path(build_env, py_bin_dir)

    # --- test environment: load the plugin, resolve the hook symbol ------------
    raw_tsv = work / "pstrace_raw.tsv"
    tests_json = work / "pstrace_tests.json"
    test_env = dict(os.environ)
    _prepend_path(test_env, py_bin_dir)
    # Only add the pstrace package (for ``-p pstrace.plugin``); do NOT put the
    # project root on PYTHONPATH. For an inplace build the extension is already
    # importable via the test command's cwd, and for an installed build (e.g.
    # meson-python -> site-packages) a source dir on the path would shadow the
    # installed, instrumented package with an unbuilt copy.
    _prepend_pythonpath(test_env, str(_REPO))
    addopts = test_env.get("PYTEST_ADDOPTS", "")
    test_env["PYTEST_ADDOPTS"] = (addopts + " -p pstrace.plugin").strip()
    test_env["PSTRACE_OUTPUT"] = str(raw_tsv)
    test_env["PSTRACE_TESTS"] = str(tests_json)
    if use_preload:
        # Put libpstrace in the global scope so instrumented extensions resolve
        # the hook there, and point the plugin straight at it.
        preload_var = "DYLD_INSERT_LIBRARIES" if sys.platform == "darwin" else "LD_PRELOAD"
        existing_preload = test_env.get(preload_var, "")
        test_env[preload_var] = (
            f"{hook_lib}{os.pathsep}{existing_preload}" if existing_preload else str(hook_lib)
        )
        if sys.platform == "darwin":
            test_env["DYLD_FORCE_FLAT_NAMESPACE"] = "1"
        test_env["PSTRACE_LIB"] = str(hook_lib)
    else:
        if args.module:
            test_env["PSTRACE_MODULE"] = args.module
        if args.lib:
            test_env["PSTRACE_LIB"] = args.lib

    try:
        _run(args.build_cmd, cwd=project_root, env=build_env, label="build")
    except DriverError as exc:
        print(f"pstrace-driver: {exc}", file=sys.stderr)
        return 1

    # Test failures are tolerated: the passing-tests sidecar drops failed/errored
    # tests from the coverage universe anyway, and a run with a few red tests
    # still yields a valid trace. A hard error (e.g. pytest missing) instead
    # produces no trace and is caught by the raw-trace check below.
    print(f"pstrace-driver: [test] $ {args.test_cmd}  (cwd={test_cwd})", file=sys.stderr)
    test_proc = subprocess.run(args.test_cmd, cwd=str(test_cwd), env=test_env, shell=True)
    if test_proc.returncode != 0:
        print(f"pstrace-driver: test command exited {test_proc.returncode} "
              "(continuing; only passing tests are kept in the coverage map)",
              file=sys.stderr)

    if not raw_tsv.is_file() or raw_tsv.stat().st_size == 0:
        print(f"pstrace-driver: no trace produced at {raw_tsv}; was the extension "
              "rebuilt with the wrapper and PSTRACE_MODULE/--lib correct?",
              file=sys.stderr)
        return 1

    # --- report: raw trace -> coverage map -------------------------------------
    report_env = dict(os.environ)
    _prepend_pythonpath(report_env, str(_REPO))
    report_cmd = [
        args.python, "-m", "pstrace.report",
        "--raw", str(raw_tsv),
        "--src-root", str(Path(args.src_root).resolve()),
        "--project-root", str(project_root),
        "--tests", str(tests_json),
        "--coverage-json", str(Path(args.coverage_json).resolve()),
    ]
    for keep in args.keep_file:
        report_cmd += ["--keep-file", keep]
    print(f"pstrace-driver: [report] $ {' '.join(shlex.quote(c) for c in report_cmd)}",
          file=sys.stderr)
    proc = subprocess.run(report_cmd, env=report_env)
    if proc.returncode != 0:
        print("pstrace-driver: report failed", file=sys.stderr)
        return 1

    print(f"pstrace-driver: done -> {Path(args.coverage_json).resolve()}", file=sys.stderr)
    print(f"pstrace-driver: artifacts in {work}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

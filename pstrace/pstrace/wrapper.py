"""Build-system-agnostic compiler wrapper that injects pstrace instrumentation.

Stand in for the C/C++ compiler (``CC`` / ``CXX``). On a step that compiles a
source it appends ``-finstrument-functions`` (plus ``-O0 -g ...``) and the
pstrace include dir; on a shared-object link step it adds the prebuilt hook
object so ``__cyg_profile_func_enter`` and ``pstrace_set_test`` are defined in
the produced ``.so`` / ``.dylib`` / ``.bundle``. Everything else (executable
links, version probes, preprocess-only runs) is delegated untouched.

Configured entirely through the environment, so any build system that honours
``CC`` / ``CXX`` (setuptools, Meson, CMake, autotools) picks it up:

  PSTRACE_REAL_CC / PSTRACE_REAL_CXX  real compiler to delegate to (default cc/c++)
  PSTRACE_HOOK_OBJ                    prebuilt pstrace_hook.o linked into shared libs
  PSTRACE_INCLUDE                     os.pathsep-joined extra include dirs
  PSTRACE_FLAGS                       instrumentation flags (default: DEFAULT_FLAGS)
  PSTRACE_EXTRA_FLAGS                 extra flags appended verbatim
  PSTRACE_DEBUG                       if set, log the rewritten command to stderr

Configure-time try-compile/link probes (Meson ``meson-private``, CMake scratch
dirs, distutils ``_configtest``) are passed through untouched: instrumenting
them would leave the hook symbols undefined in a build system's own tiny test
programs and break its compiler detection.
"""

from __future__ import annotations

import os
import shlex
import sys

#: Default instrumentation flags. ``-O0`` keeps functions from being inlined
#: away so every one stays observable; ``-g`` / frame pointers let the offline
#: symbolizer recover file:line.
DEFAULT_FLAGS = ["-finstrument-functions", "-O0", "-g", "-fno-omit-frame-pointer"]

#: Flags that mark a shared-object link (where the hook object must be added).
#: ``-bundle`` is how CPython links extensions on macOS; ``-shared`` on Linux.
_LINK_SHARED_FLAGS = ("-shared", "-bundle", "-dynamiclib")

#: C/C++/ObjC source extensions that mean this invocation compiles a source.
_SRC_EXTS = (".c", ".cc", ".cpp", ".cxx", ".c++", ".m", ".mm")

#: Path substrings that mark a build system's throwaway probe compile/link. We
#: must not instrument these, or the probe's own tiny program fails to resolve
#: the hook symbols and the build system aborts its compiler checks.
_CHECK_MARKERS = (
    "meson-private",
    "CMakeScratch",
    "CMakeTmp",
    "CMakeFiles/Check",
    "_configtest",
    "conftest",
)


def _split_paths(name: str) -> list[str]:
    return [p for p in os.environ.get(name, "").split(os.pathsep) if p]


def _looks_like_check(args: list[str]) -> bool:
    return any(marker in a for a in args for marker in _CHECK_MARKERS)


def build_command(lang: str, args: list[str]) -> list[str]:
    """Return the full argv (real compiler + rewritten args) for ``args``.

    Pure function of ``args`` and the environment so it can be unit-tested
    without spawning a compiler.
    """
    real_var = "PSTRACE_REAL_CXX" if lang == "cxx" else "PSTRACE_REAL_CC"
    real = os.environ.get(real_var) or ("c++" if lang == "cxx" else "cc")
    real_argv = shlex.split(real)

    flags_env = os.environ.get("PSTRACE_FLAGS")
    flags = shlex.split(flags_env) if flags_env else list(DEFAULT_FLAGS)
    flags += shlex.split(os.environ.get("PSTRACE_EXTRA_FLAGS", ""))
    includes = _split_paths("PSTRACE_INCLUDE")
    hook_obj = os.environ.get("PSTRACE_HOOK_OBJ", "")

    new_args = list(args)

    is_object_compile = "-c" in args
    is_shared_link = any(a in _LINK_SHARED_FLAGS for a in args)
    has_source = any(a.endswith(_SRC_EXTS) for a in args)
    # A source is compiled here on a plain ``-c`` step or a combined
    # compile-and-link of a shared object; executable links are left alone.
    compiles_source = is_object_compile or (is_shared_link and has_source)

    if (is_object_compile or is_shared_link) and not _looks_like_check(args):
        if compiles_source:
            for inc in includes:
                new_args += ["-I", inc]
            new_args += flags
        if is_shared_link and hook_obj:
            new_args.append(hook_obj)
            # glibc < 2.34 keeps dladdr in libdl; harmless on newer glibc.
            if sys.platform.startswith("linux"):
                new_args.append("-ldl")

    return real_argv + new_args


def main(lang: str) -> int:
    cmd = build_command(lang, sys.argv[1:])
    if os.environ.get("PSTRACE_DEBUG"):
        sys.stderr.write(
            "pstrace-wrapper: " + " ".join(shlex.quote(c) for c in cmd) + "\n"
        )
    try:
        os.execvp(cmd[0], cmd)
    except OSError as exc:  # exec replaces the process; only reached on failure
        sys.stderr.write(f"pstrace-wrapper: cannot exec {cmd[0]!r}: {exc}\n")
        return 127
    return 0  # unreachable

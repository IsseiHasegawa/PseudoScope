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
  PSTRACE_TARGET                      os.pathsep-joined path substrings; when set,
                                      only sources whose path matches one of them
                                      are instrumented (the rest build normally)
  PSTRACE_HOOK_LINK_MATCH            only add the hook to a shared-lib link whose
                                      output name contains this substring; keeps a
                                      single hook instance in a multi-extension
                                      project (empty = every shared link, the
                                      default for single-extension targets)
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


def _source_args(args: list[str]) -> list[str]:
    return [a for a in args if a.endswith(_SRC_EXTS)]


def _instruments_these_sources(args: list[str], targets: list[str]) -> bool:
    """Whether this compile's source(s) should be instrumented under ``targets``.

    An empty ``targets`` means "instrument everything" (the default). Otherwise a
    source is instrumented only when its path contains one of the target
    substrings, so a large project can be built normally except for the subtree
    under analysis.
    """
    if not targets:
        return True
    return any(t in s for s in _source_args(args) for t in targets)


def _output_path(args: list[str]) -> str | None:
    for i, a in enumerate(args):
        if a == "-o" and i + 1 < len(args):
            return args[i + 1]
    return None


def _link_wants_hook(args: list[str], hook_match: str) -> bool:
    """Whether this shared link should receive the hook object.

    With no ``hook_match`` every shared link gets it (correct for a single
    extension). With a match, only the link whose ``-o`` output contains the
    substring does, so the hook is defined exactly once in a multi-extension
    project instead of once per ``.so`` (which would split the trace state).
    """
    if not hook_match:
        return True
    out = _output_path(args)
    return out is not None and hook_match in out


# --- MSVC / clang-cl (Windows) ------------------------------------------------
# Not yet exercised end-to-end (no Windows toolchain here); the argument mapping
# below is unit-tested. clang-cl is Clang, so it can use -finstrument-functions
# and the existing __cyg_profile hook. Real ``cl`` has no such flag: /Gh /GH make
# it call _penter / _pexit instead, which need a separate hook that is not built
# yet (see docs/windows-msvc.md).
_MSVC_INSTR_FLAGS = ["/Gh", "/GH", "/Od", "/Zi"]
_CLANG_CL_INSTR_FLAGS = [
    "/clang:-finstrument-functions", "/clang:-fno-omit-frame-pointer", "/Od", "/Zi",
]
#: Flags that make an MSVC link emit a DLL (a Python ``.pyd`` is a DLL).
_MSVC_DLL_FLAGS = ("/LD", "/LDd", "/DLL")


def _compiler_flavor(real_argv: list[str]) -> str:
    """Classify the delegate compiler: ``msvc``, ``clang-cl``, or ``gcc``.

    ``gcc`` covers gcc/clang/cc (the GNU ``-flag`` CLI); ``cl`` and ``clang-cl``
    use the ``/flag`` CLI and different instrumentation switches.
    """
    name = os.path.basename(real_argv[0]).lower() if real_argv else ""
    if name.endswith(".exe"):
        name = name[:-4]
    if name == "clang-cl":
        return "clang-cl"
    if name == "cl":
        return "msvc"
    return "gcc"


def _msvc_makes_dll(args: list[str]) -> bool:
    if any(a in _MSVC_DLL_FLAGS for a in args):
        return True
    return any(a.lower().endswith((".dll", ".pyd")) for a in args)


def _msvc_link_wants_hook(args: list[str], hook_match: str) -> bool:
    # MSVC names its output via /Fe: or /OUT:, not -o, so match the substring
    # against any argument to single out one extension.
    if not hook_match:
        return True
    return any(hook_match in a for a in args)


def _rewrite_msvc(flavor: str, args: list[str], includes: list[str], hook_obj: str,
                  targets: list[str], hook_match: str, extra: list[str]) -> list[str]:
    """Rewrite an MSVC / clang-cl invocation: instrument compiles, hook DLL links."""
    new_args = list(args)
    if _looks_like_check(args):
        return new_args

    is_compile = "/c" in args or "-c" in args
    is_dll_link = _msvc_makes_dll(args)
    has_source = any(a.endswith(_SRC_EXTS) for a in args)
    compiles_source = is_compile or (is_dll_link and has_source)

    if compiles_source and _instruments_these_sources(args, targets):
        for inc in includes:
            new_args.append(f"/I{inc}")
        instr = _CLANG_CL_INSTR_FLAGS if flavor == "clang-cl" else _MSVC_INSTR_FLAGS
        new_args += instr + extra
    if is_dll_link and hook_obj and _msvc_link_wants_hook(args, hook_match):
        new_args.append(hook_obj)
    return new_args


def build_command(lang: str, args: list[str]) -> list[str]:
    """Return the full argv (real compiler + rewritten args) for ``args``.

    Pure function of ``args`` and the environment so it can be unit-tested
    without spawning a compiler.
    """
    real_var = "PSTRACE_REAL_CXX" if lang == "cxx" else "PSTRACE_REAL_CC"
    real = os.environ.get(real_var) or ("c++" if lang == "cxx" else "cc")
    real_argv = shlex.split(real)

    includes = _split_paths("PSTRACE_INCLUDE")
    hook_obj = os.environ.get("PSTRACE_HOOK_OBJ", "")
    targets = _split_paths("PSTRACE_TARGET")
    hook_match = os.environ.get("PSTRACE_HOOK_LINK_MATCH", "")
    extra = shlex.split(os.environ.get("PSTRACE_EXTRA_FLAGS", ""))

    flavor = _compiler_flavor(real_argv)
    if flavor != "gcc":
        return real_argv + _rewrite_msvc(
            flavor, args, includes, hook_obj, targets, hook_match, extra
        )

    flags_env = os.environ.get("PSTRACE_FLAGS")
    flags = shlex.split(flags_env) if flags_env else list(DEFAULT_FLAGS)
    flags += extra

    new_args = list(args)

    is_object_compile = "-c" in args
    is_shared_link = any(a in _LINK_SHARED_FLAGS for a in args)
    has_source = any(a.endswith(_SRC_EXTS) for a in args)
    # A source is compiled here on a plain ``-c`` step or a combined
    # compile-and-link of a shared object; executable links are left alone.
    compiles_source = is_object_compile or (is_shared_link and has_source)

    if (is_object_compile or is_shared_link) and not _looks_like_check(args):
        if compiles_source and _instruments_these_sources(args, targets):
            for inc in includes:
                new_args += ["-I", inc]
            new_args += flags
        if is_shared_link and hook_obj and _link_wants_hook(args, hook_match):
            new_args.append(hook_obj)
            # The hook uses dladdr and pthreads; both live in libc on glibc >=2.34
            # (so these are empty archives there) but older systems need them.
            if sys.platform.startswith("linux"):
                new_args += ["-ldl", "-lpthread"]

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

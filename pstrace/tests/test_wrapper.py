"""Unit tests for the pstrace compiler wrapper's argument rewriting.

``build_command`` is a pure function of its args and the environment, so these
exercise every classification branch without spawning a real compiler.
"""

from __future__ import annotations

import pytest

from pstrace.wrapper import build_command


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PSTRACE_REAL_CC", "clang")
    monkeypatch.setenv("PSTRACE_REAL_CXX", "clang++")
    monkeypatch.setenv("PSTRACE_HOOK_OBJ", "/hook.o")
    monkeypatch.setenv("PSTRACE_INCLUDE", "/inc")
    monkeypatch.delenv("PSTRACE_FLAGS", raising=False)
    monkeypatch.delenv("PSTRACE_EXTRA_FLAGS", raising=False)


def test_compile_step_gets_instrumentation_and_include():
    out = build_command("c", ["-c", "foo.c", "-o", "foo.o"])
    assert out[0] == "clang"
    assert "-finstrument-functions" in out
    assert "-I" in out and "/inc" in out
    assert "/hook.o" not in out  # the hook belongs on the link, not a compile


def test_shared_link_gets_hook_only():
    out = build_command("c", ["-shared", "foo.o", "-o", "foo.so"])
    assert "/hook.o" in out
    # no source is compiled here, so no instrumentation flags are added
    assert "-finstrument-functions" not in out


def test_macos_bundle_counts_as_a_shared_link():
    out = build_command("c", ["-bundle", "-undefined", "dynamic_lookup", "x.o", "-o", "x.so"])
    assert "/hook.o" in out


def test_combined_compile_and_shared_link_gets_both():
    out = build_command("c", ["-shared", "foo.c", "-o", "foo.so"])
    assert "-finstrument-functions" in out
    assert "/hook.o" in out


def test_executable_link_is_passed_through():
    # We never trace standalone executables, so leave them untouched.
    assert build_command("c", ["foo.c", "-o", "foo"]) == ["clang", "foo.c", "-o", "foo"]


def test_build_system_probe_is_passed_through():
    args = ["-c", "/b/meson-private/tmpX/testfile.c", "-o", "/b/meson-private/tmpX/o.o"]
    assert build_command("c", args) == ["clang"] + args


def test_version_query_is_passed_through():
    assert build_command("c", ["--version"]) == ["clang", "--version"]


def test_cxx_delegates_to_real_cxx():
    out = build_command("cxx", ["-c", "foo.cpp", "-o", "foo.o"])
    assert out[0] == "clang++"


def test_flags_are_overridable(monkeypatch):
    monkeypatch.setenv("PSTRACE_FLAGS", "-finstrument-functions -O1")
    out = build_command("c", ["-c", "a.c", "-o", "a.o"])
    assert "-O1" in out and "-O0" not in out


def test_target_allowlist_instruments_only_matching_sources(monkeypatch):
    monkeypatch.setenv("PSTRACE_TARGET", "src/multiarray")
    inside = build_command("c", ["-c", "../numpy/_core/src/multiarray/item.c", "-o", "item.o"])
    outside = build_command("c", ["-c", "../numpy/_core/src/umath/loops.c", "-o", "loops.o"])
    assert "-finstrument-functions" in inside
    assert "-finstrument-functions" not in outside  # built normally


def test_hook_link_match_limits_hook_to_one_extension(monkeypatch):
    monkeypatch.setenv("PSTRACE_HOOK_LINK_MATCH", "_multiarray_umath")
    hooked = build_command("c", ["-bundle", "a.o", "-o", "_multiarray_umath.cpython-314.so"])
    other = build_command("c", ["-bundle", "b.o", "-o", "_umath_tests.cpython-314.so"])
    assert "/hook.o" in hooked
    assert "/hook.o" not in other  # avoids a second hook copy that splits state


# -- MSVC / clang-cl (Windows) flag mapping; argv-level only, no Windows here --


def test_msvc_compile_gets_penter_flags(monkeypatch):
    monkeypatch.setenv("PSTRACE_REAL_CC", "cl")
    out = build_command("c", ["/c", "foo.c", "/Fo:foo.obj"])
    assert out[0] == "cl"
    assert all(f in out for f in ("/Gh", "/GH", "/Od", "/Zi"))
    assert "/I/inc" in out
    assert "-finstrument-functions" not in " ".join(out)  # cl has no such flag


def test_clang_cl_compile_uses_finstrument(monkeypatch):
    monkeypatch.setenv("PSTRACE_REAL_CC", "clang-cl.exe")
    out = build_command("c", ["/c", "foo.c", "/Fo:foo.obj"])
    assert out[0] == "clang-cl.exe"
    assert "/clang:-finstrument-functions" in out
    assert "/Od" in out and "/Zi" in out
    assert "/Gh" not in out  # clang-cl reuses the __cyg_profile hook, not _penter


def test_msvc_dll_link_gets_hook(monkeypatch):
    monkeypatch.setenv("PSTRACE_REAL_CC", "cl")
    out = build_command("c", ["/LD", "foo.obj", "/Fe:foo.pyd"])
    assert "/hook.o" in out
    assert "/Gh" not in out  # no source compiled at the link


def test_clang_cl_combined_compile_and_dll_gets_both(monkeypatch):
    monkeypatch.setenv("PSTRACE_REAL_CC", "clang-cl")
    out = build_command("c", ["/LD", "foo.c", "/Fe:foo.pyd"])
    assert "/clang:-finstrument-functions" in out
    assert "/hook.o" in out


def test_msvc_probe_is_passed_through(monkeypatch):
    monkeypatch.setenv("PSTRACE_REAL_CC", "cl")
    args = ["/c", r"C:\build\meson-private\tmp\testfile.c", "/Fo:o.obj"]
    assert build_command("c", args) == ["cl"] + args


def test_msvc_executable_link_is_passed_through(monkeypatch):
    monkeypatch.setenv("PSTRACE_REAL_CC", "cl")
    out = build_command("c", ["foo.obj", "/Fe:foo.exe"])
    assert out == ["cl", "foo.obj", "/Fe:foo.exe"]  # not a DLL, left alone


def test_msvc_hook_link_match_selects_one_extension(monkeypatch):
    monkeypatch.setenv("PSTRACE_REAL_CC", "cl")
    monkeypatch.setenv("PSTRACE_HOOK_LINK_MATCH", "_core")
    hooked = build_command("c", ["/LD", "a.obj", "/Fe:_core.pyd"])
    other = build_command("c", ["/LD", "b.obj", "/Fe:other.pyd"])
    assert "/hook.o" in hooked
    assert "/hook.o" not in other

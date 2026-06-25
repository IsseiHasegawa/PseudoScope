"""pytest plugin: attribute instrumented native calls to the running test.

Load it for a session with ``-p pstrace.plugin``. The plugin resolves the
``pstrace_set_test`` symbol exported by the instrumented extension and calls it
at the start of every test's protocol, so the C hook buckets each function
entry under the current test's nodeid.

Target resolution (first that works wins):
  * ``PSTRACE_LIB``    — explicit path to the built ``.so``
  * ``PSTRACE_MODULE`` — importable module whose ``__file__`` is the ``.so``
  * the global symbol namespace (already-loaded images)

If no instrumented symbol is found the plugin disables itself quietly, so it is
safe to load against a normal (non-instrumented) build.

Output goes to ``$PSTRACE_OUTPUT`` (default ``pstrace_raw.tsv``), written when
the session finishes (and again at process exit; the second write is a no-op).

Alongside the raw trace, the plugin records which tests *passed* and writes them
as a JSON array to ``$PSTRACE_TESTS`` (default ``pstrace_tests.json``). The
offline report uses this sidecar to drop tests that failed/errored/were skipped
from the coverage map: a mutant judged against an already-failing test is
meaningless, so only passing tests belong in the selection universe.
"""

from __future__ import annotations

import ctypes
import importlib
import json
import os

import pytest

_set_test = None  # ctypes function or None when disabled
_dump = None
_passing: set[str] = set()  # nodeids whose call phase passed


def _resolve():
    """Find (pstrace_set_test, pstrace_dump) from the instrumented build."""
    candidates: list[str | None] = []

    lib_path = os.environ.get("PSTRACE_LIB")
    if lib_path:
        candidates.append(lib_path)

    module_name = os.environ.get("PSTRACE_MODULE")
    if module_name:
        try:
            mod = importlib.import_module(module_name)
            if getattr(mod, "__file__", None):
                candidates.append(mod.__file__)
        except Exception:  # noqa: BLE001 - importing the target is best-effort
            pass

    candidates.append(None)  # global namespace (RTLD_DEFAULT)

    for candidate in candidates:
        try:
            lib = ctypes.CDLL(candidate)
            set_test = lib.pstrace_set_test
            set_test.argtypes = [ctypes.c_char_p]
            set_test.restype = None
            dump = lib.pstrace_dump
            dump.argtypes = []
            dump.restype = None
            return set_test, dump
        except (OSError, AttributeError):
            continue
    return None, None


def pytest_configure(config: pytest.Config) -> None:
    global _set_test, _dump
    _set_test, _dump = _resolve()
    config.addinivalue_line("markers", "pstrace: per-test native tracing marker")
    if _set_test is None:
        config.issue_config_time_warning(
            UserWarning(
                "pstrace: no instrumented 'pstrace_set_test' symbol found; "
                "tracing disabled. Build the target with PSTRACE_ENABLE=1 and "
                "set PSTRACE_MODULE or PSTRACE_LIB."
            ),
            stacklevel=2,
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem):
    """Mark the current test before its setup/call/teardown run."""
    if _set_test is not None:
        _set_test(item.nodeid.encode("utf-8"))
    yield


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Record a nodeid as passing iff its call phase passed.

    A test that errors in setup, fails, or is skipped never reaches a passing
    call phase, so it is excluded from the passing set (and thus from the
    coverage map's selection universe).
    """
    if report.when == "call" and report.passed:
        _passing.add(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus) -> None:
    if _set_test is not None:
        _set_test(b"")  # detach: later interpreter-shutdown calls are not a test
    if _dump is not None:
        _dump()

    # Write the passing-test sidecar. A failure here must never break the
    # session, so swallow any error.
    tests_path = os.environ.get("PSTRACE_TESTS", "pstrace_tests.json")
    try:
        with open(tests_path, "w", encoding="utf-8") as fh:
            json.dump(sorted(_passing), fh, indent=2)
            fh.write("\n")
    except Exception:  # noqa: BLE001 - sidecar write is best-effort
        pass

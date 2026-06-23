"""pstrace — per-test native function tracing via -finstrument-functions.

A target C/C++ extension is built with ``-finstrument-functions`` and linked
against ``src/pstrace_hook.c``. The :mod:`pstrace.plugin` pytest plugin marks
each test boundary; the hook attributes every instrumented function entry to
the current test. :mod:`pstrace.symbolize` turns the raw address table into a
per-test ``(test_id, function, file, line, count)`` table.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"

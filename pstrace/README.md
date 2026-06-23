# pstrace

Per-test **native** function tracing for C/C++ extensions exercised through
`pytest`. Answers: *which test actually executed which C/C++ function?* — at full
call depth, including `static` internal functions.

Unlike [`count-function-call`](../count-function-call) (which inserts a macro
into each function body), pstrace is **non-invasive**: it adds nothing to your
sources. The compiler's `-finstrument-functions` inserts the entry hook for
every function automatically.

```
pytest run ─▶ C hook records (test, function-address)+count ─▶ raw TSV
                                                                  │
                                          atos / addr2line + source filter
                                                                  ▼
                          per-test table: test_id, function, file, line, count
```

## What you get

A long-format ("tidy") table, one row per `(test, function)` that actually ran:

| test_id | function | file | line | count |
|---|---|---|---|---|
| `tests/test_ujson.py::test_encode_dict_conversion` | `Dict_iterNext` | `objToJSON.c` | 261 | 7 |
| `tests/test_ujson.py::test_loads[…]` | `decode_array` | `ultrajsondec.c` | 535 | 1 |

Pivot it either way: `test → functions` or `function → tests`.

This is the **dynamic** ("executed") view — a subset of what static reachability
would list, and it captures real run-time paths static analysis misses (e.g. a
test that does an encode↔decode round-trip pulls in both encoder and decoder).

## How it works

| Part | File | Role |
|---|---|---|
| Hook | `src/pstrace_hook.c` | `__cyg_profile_func_enter` buckets each entry under the current test; dumps `(test, image, offset, count)` at exit |
| API | `include/pstrace.h` | `pstrace_set_test(id)` / `pstrace_dump()` |
| Plugin | `pstrace/plugin.py` | pytest plugin: calls `pstrace_set_test(nodeid)` around every test (via `ctypes`) |
| Symbolize | `pstrace/symbolize.py` | `(image, offset) → function/file:line` (atos on macOS, addr2line on Linux), then keep only the target's own sources |
| Build | `pstrace/build.py` | `python -m pstrace.build` — inject instrumentation into a target's build **without editing its `setup.py`** |
| CLI | `pstrace/report.py` | `pstrace-report` — raw TSV → per-test CSV |

Addresses are recorded as `offset = this_fn − image_base`, so ASLR is irrelevant
and symbolization happens after the run against the on-disk binary.

## Build integration (no edits to the target)

`pstrace.build` instruments the target **without touching its `setup.py`**. It
monkeypatches setuptools so the chosen extension gets `pstrace_hook.c` added as a
source and `-finstrument-functions -O0 -g` appended, then runs the target's own
`setup.py`. `-O0` keeps functions from being inlined away so every one is
observable. Everything after `--` is the target's normal `setup.py` command line.

```bash
# ujson — single extension.
# (ujson's objToJSON.c #includes the ncc shim header, so pass that include dir.)
cd ultrajson
PYTHONPATH=../pstrace .venv/bin/python -m pstrace.build \
  --target ujson --include ../count-function-call/include \
  -- setup.py build_ext --inplace --force

# Pillow — many extensions: instrument exactly one by name.
cd Pillow
touch src/_imaging.c src/libImaging/*.c     # force re-instrument (distutils is mtime-based)
PYTHONPATH=../pstrace .venv/bin/python -m pstrace.build \
  --target PIL._imaging -- setup.py build_clib build_ext --inplace
```

| Driver flag | Meaning |
|---|---|
| `--target EXT` | extension to instrument (repeatable; omit = all) |
| `--include DIR` | extra include dir for the instrumented extension (repeatable) |
| `--flag CFLAG` | extra compile flag (repeatable) |
| `--repo PATH` | pstrace repo root (default: this package's location) |

To get an ordinary, un-instrumented build back, just run the target's own
`setup.py` without the driver (the target's files are never modified).

> Clang has no `-finstrument-functions-exclude-file-list` (that's GCC-only), so
> bundled C++ deps (e.g. double-conversion) get instrumented too. They are
> removed later by the source filter, not at compile time. Cost is negligible.

## Run

```bash
cd ultrajson
PYTHONPATH=../pstrace \
PSTRACE_MODULE=ujson \
PSTRACE_OUTPUT=pstrace_raw.tsv \
  .venv/bin/python -m pytest tests/ -p pstrace.plugin

PYTHONPATH=../pstrace .venv/bin/python -m pstrace.report \
  --raw pstrace_raw.tsv --src-root src/ujson --out per_test.csv
```

| Env / flag | Meaning |
|---|---|
| `-p pstrace.plugin` | load the boundary-marking plugin |
| `PSTRACE_MODULE` | importable module whose `.so` exports `pstrace_set_test` (or `PSTRACE_LIB=<path>`) |
| `PSTRACE_OUTPUT` | raw TSV path (default `pstrace_raw.tsv`) |
| `--src-root` | keep only functions defined under this tree; `deps`/`double-conversion` subtrees are dropped |
| `--keep-file BASENAME` | keep an extra source file by basename (repeatable) |
| `--no-filter` | keep every resolved function (skip the source filter) |

## Limitations

- **Same process / thread only.** Calls in a `subprocess` or a spawned thread
  are not attributed (the conversation's known blind spot). Serial pytest is
  assumed; the counter is not thread-safe.
- **`-O0` view.** Reflects un-inlined structure, slightly broader than an `-O2`
  build. Use `-O1` if you want closer-to-release call structure.
- **Pre-test calls** (module import) land under a synthetic `(startup)` id.
- Requires non-stripped symbols (`atos`/`addr2line` read them). macOS keeps
  them by default; on Linux build with `UJSON_BUILD_NO_STRIP=1`.

## Relation to pseudoscope

This table tells the mutation/pseudo-test workflow **which tests exercise a
given C function**, so a mutation of function `F` can be checked against just the
tests that actually run `F`.

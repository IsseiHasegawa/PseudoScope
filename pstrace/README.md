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

## Any build system: the pstrace driver

`pstrace.build` only works with setuptools. For everything else, `pstrace.driver`
does the whole run (build → traced pytest → coverage map) against **any build
system that honors `CC` / `CXX`**: setuptools, Meson / meson-python, CMake /
scikit-build-core, autotools. It points the toolchain at a compiler wrapper
(`pstrace-cc` / `pstrace-cxx`) that appends `-finstrument-functions -O0 -g` to
compiles and links the hook object into the extension. Build-system probe
compiles (Meson `meson-private`, CMake scratch dirs, distutils `_configtest`) and
executable links are passed through untouched, so compiler detection keeps
working.

```bash
# setuptools (inplace build: importable from the project cwd)
python -m pstrace.driver \
  --project-root ultrajson --python ultrajson/.venv/bin/python \
  --module ujson --src-root ultrajson/src/ujson \
  --build-cmd "python setup.py build_ext --inplace --force" \
  --test-cmd  "python -m pytest tests/" \
  --coverage-json coverage.json

# Meson / meson-python (large, multi-extension): instrument one subtree, keep the
# hook in one .so, run tests from a neutral cwd because the package is installed.
python -m pstrace.driver \
  --project-root numpy --python .venv/bin/python \
  --module numpy._core._multiarray_umath \
  --src-root numpy/numpy/_core/src/multiarray \
  --instrument-path src/multiarray --hook-in _multiarray_umath \
  --build-cmd "pip install -e . --no-build-isolation -Csetup-args=-Dallow-noblas=true" \
  --test-cmd  "python -m pytest --pyargs numpy._core.tests.test_multiarray" \
  --test-dir /tmp --coverage-json coverage.json

# CMake / scikit-build-core: disable install stripping so symbols survive.
python -m pstrace.driver \
  --project-root pkg --python .venv/bin/python \
  --module pkg._core --src-root pkg \
  --build-cmd "pip install -e . --no-build-isolation -Cinstall.strip=false" \
  --test-cmd  "python -m pytest tests/" --test-dir /tmp --coverage-json coverage.json
```

| Driver flag | Meaning |
|---|---|
| `--build-cmd` / `--test-cmd` | shell commands for a clean rebuild and the pytest run |
| `--module` / `--lib` | importable module (or explicit `.so`) that exports the hook |
| `--src-root` / `--project-root` | keep functions under this tree; make coverage keys relative to the root |
| `--instrument-path SUB` | only instrument sources matching `SUB` (repeatable; default: all) |
| `--hook-in SUB` | link the hook into only the `.so` whose name matches `SUB` (link mode) |
| `--hook-mode` | `auto` (default) / `link` / `preload`; `auto` = preload on Linux, link on macOS |
| `--test-dir DIR` | cwd for the test command (default: project root; set neutral for installed pkgs) |
| `--keep-file BASENAME` | keep an extra source basename (e.g. Cython-generated `.c`) |

**Symbolization needs the build to leave debug info reachable.** The offline
symbolizer (`atos` / `addr2line`) must find `-g` debug data:

- **Linux** embeds DWARF in the `.so`; nothing extra is needed.
- **macOS** keeps DWARF in the `.o` files and references them from the `.so`, so
  the object files must survive the build (use an **editable** install, not a
  throwaway `pip install .` that deletes its build dir) and the binary must not
  be **stripped** (scikit-build-core strips by default: `-Cinstall.strip=false`).

Validated on Clang/GCC, Linux/macOS. **Windows is not supported yet**: the
wrapper already maps the compile flags (`clang-cl` -> `-finstrument-functions`,
which reuses the existing hook; `cl.exe` -> `/Gh /GH`, which needs a separate
`_penter` / `_pexit` hook), but the hook, linking, and PDB symbolization are not
built. See [`docs/windows-msvc.md`](docs/windows-msvc.md) for the plan. The hook
reaches the extension one of two ways (`--hook-mode`,
default `auto`): **link injection** builds the hook into the `.so`, while
**preload** loads a shared `libpstrace` via `LD_PRELOAD` / `DYLD_INSERT_LIBRARIES`.
Linux requires preload (a hook linked into a CPython extension is loaded
`RTLD_LOCAL` and its `__cyg_profile_func_enter` is never reached), so `auto`
selects preload there and link injection on macOS. Preload also shares one hook
across **several** instrumented `.so`s, so it is the way to instrument more than
one extension at once. That path is **Linux-only**: on macOS System Integrity
Protection strips `DYLD_INSERT_LIBRARIES` when the shell-run test command execs a
protected binary, so preload cannot inject the hook. macOS is therefore limited to
link injection (one extension per run, selected with `--instrument-path` /
`--hook-in`); instrumenting several extensions at once needs Linux.

## Run

```bash
cd ultrajson
PYTHONPATH=../pstrace \
PSTRACE_MODULE=ujson \
PSTRACE_OUTPUT=pstrace_raw.tsv \
PSTRACE_TESTS=pstrace_tests.json \
  .venv/bin/python -m pytest tests/ -p pstrace.plugin

PYTHONPATH=../pstrace .venv/bin/python -m pstrace.report \
  --raw pstrace_raw.tsv --src-root src/ujson --out per_test.csv
```

| Env / flag | Meaning |
|---|---|
| `-p pstrace.plugin` | load the boundary-marking plugin |
| `PSTRACE_MODULE` | importable module whose `.so` exports `pstrace_set_test` (or `PSTRACE_LIB=<path>`) |
| `PSTRACE_OUTPUT` | raw TSV path (default `pstrace_raw.tsv`) |
| `PSTRACE_TESTS` | passing-nodeids sidecar path (default `pstrace_tests.json`) |
| `--src-root` | keep only functions defined under this tree; `deps`/`double-conversion` subtrees are dropped |
| `--keep-file BASENAME` | keep an extra source file by basename (repeatable) |
| `--no-filter` | keep every resolved function (skip the source filter) |

## Coverage map for PseudoClang

The report can also emit a coverage-map JSON (`pstrace-coverage/1`) that answers
*which tests exercise function `F` in file `P`?* with zero transformation on the
consumer side. It is keyed by `(project-root-relative source path, bare function
name)` and lists pytest nodeids:

```bash
PYTHONPATH=../pstrace .venv/bin/python -m pstrace.report \
  --raw pstrace_raw.tsv --src-root src/ujson \
  --project-root . --tests pstrace_tests.json \
  --coverage-json coverage.json
```

```json
{
  "meta": { "schema": "pstrace-coverage/1", "project_root": "/abs/ultrajson",
            "image": "ujson.cpython-314-darwin.so", "...": "..." },
  "coverage": {
    "src/ujson/python/objToJSON.c": {
      "Object_beginTypeContext": ["tests/test_ujson.py::test_encode_dict_values"]
    }
  },
  "tests": ["tests/test_ujson.py::test_dumps", "..."]
}
```

| Flag | Meaning |
|---|---|
| `--coverage-json PATH` | emit the coverage-map JSON (in addition to / instead of `--out`) |
| `--project-root PATH` | target project root; coverage keys are made relative to it (**required** with `--coverage-json`) |
| `--tests PATH` | passing-nodeids sidecar from the plugin (default `pstrace_tests.json`) |

Semantics the consumer relies on:

- **File keys are project-root-relative POSIX paths**, never basenames, so two
  files with the same basename stay distinct.
- **Function keys are bare identifiers.** A demangled C++ name `ns::C::m(int)`
  reduces to `m`; overload collisions are accepted.
- **Only passing tests appear.** Failed/errored/skipped tests are dropped (a
  mutant judged against an already-failing test is meaningless). Without the
  `--tests` sidecar this filter is disabled and a warning is printed.
- **`(startup)` is preserved** inside coverage lists (import-time calls) but
  never appears in the top-level `tests` array; a function whose list is exactly
  `["(startup)"]` is startup-only.
- **Output is deterministic** (sorted keys and lists, `indent=2`). Set
  `SOURCE_DATE_EPOCH` to also pin `meta.created_at` for byte-identical diffs.

## Limitations

- **Threads are supported; subprocesses are not.** The hook keeps a per-thread
  table and merges them at dump, so calls on threads a test spawns are recorded
  (thread-safely) and attributed to the current test. Serial pytest is still
  assumed for the *test* boundary. A `subprocess` is a separate address space and
  is not attributed.
- **`-O0` view.** Reflects un-inlined structure, slightly broader than an `-O2`
  build. Use `-O1` if you want closer-to-release call structure.
- **Pre-test calls** (module import) land under a synthetic `(startup)` id.
- Requires non-stripped symbols (`atos`/`addr2line` read them). macOS keeps
  them by default; on Linux build with `UJSON_BUILD_NO_STRIP=1`.

## Relation to pseudoscope

This table tells the mutation/pseudo-test workflow **which tests exercise a
given C function**, so a mutation of function `F` can be checked against just the
tests that actually run `F`.

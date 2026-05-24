# PseudoScope

Research on **pseudo-tested** C/C++ functions in [UltraJSON](https://github.com/ultrajson/ultrajson), inspired by [PSEUDOSWEEP](https://github.com/mdecourse/PSEUDOSWEEP).

The idea: replace a function body with a default return (e.g. `return 0;`), rebuild, and run tests. If tests still pass, the function may be **pseudo-tested** (the suite does not detect the mutation).

## Repository layout

```
PesudoScope/
├── README.md
├── pseudoscope.py
├── examples/pseudoscope_demo/   # small C demo (expected PI ≈ 50%)
└── ultrajson/          # target project (nested git repo)
    ├── src/
    ├── tests/
    ├── .pseudoscope/   # function inventory (discover output)
    └── baseline_test_result.txt
```

## Requirements

- [Universal Ctags](https://github.com/universal-ctags/ctags) (`ctags` on `PATH`)
- For building and testing `ultrajson/`: Python 3.10+, a C/C++ toolchain, and `pip`

## Counting C/C++ functions with ctags

List how many functions each source file contains. Run from the project root that has `src/` (i.e. inside `ultrajson/`):

```bash
cd ultrajson

find src \( -name "*.c" -o -name "*.cpp" -o -name "*.cc" -o -name "*.cxx" \) -type f -print0 |
while IFS= read -r -d '' file; do
  count=$(ctags -x --kinds-C=f --kinds-C++=f "$file" 2>/dev/null | wc -l | tr -d ' ')
  printf "%s\t%s\n" "$file" "$count"
done
```

Output is tab-separated: `path<TAB>function_count`. Only function definitions are counted (`--kinds-C=f` / `--kinds-C++=f`).

Example (core ujson sources):

```
src/ujson/python/ujson.c        6
src/ujson/python/JSONtoObj.c    19
src/ujson/python/objToJSON.c    34
src/ujson/lib/ultrajsonenc.c    15
src/ujson/lib/ultrajsondec.c    12
src/ujson/lib/dconv_wrapper.cc  6
```

If `find` reports `No such file or directory` for `src`, `cd` into `ultrajson/` first (not the `PesudoScope/` parent alone).

## UltraJSON — build and test

`ujson` is a C extension (`ujson.cpython-*.so`). **After every change to C/C++ source under `src/`, rebuild before running tests.** `pytest` alone does not recompile; it keeps using the last built `.so`.

| Changed files | Rebuild needed? |
|---------------|-----------------|
| `src/ujson/**/*.c`, `*.cc` | Yes — run `pip install -e ".[dev]"` again |
| `tests/*.py` only | No — `pytest` is enough |

First-time setup:

```bash
cd ultrajson
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Day-to-day loop after editing C (PseudoScope mutations, restores, etc.):

```bash
cd ultrajson
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Quick sanity check that the new binary is loaded:

```bash
python3 -c "import ujson; print(ujson.dumps(3.14159))"
```

If this still prints `0.0` after you restored source, the extension was not rebuilt (or the build failed — check `pip install` output).

A passing baseline is recorded in `ultrajson/baseline_test_result.txt` (379 tests).

Function metadata for sweeps lives under `ultrajson/.pseudoscope/` (`functions.json`, `functions_lib.json`).

## Demo project (`examples/pseudoscope_demo/`)

A tiny C library with **tested** functions (`add`, `multiply`) and **untested** functions (`dead_code_transform`, `unused_scale`). A full sweep should report **PI ≈ 50%**. See [examples/pseudoscope_demo/README.md](examples/pseudoscope_demo/README.md).

## PseudoScope CLI (`pseudoscope.py`)

Mutation sweep: for each function under `src/`, replace the body with default return value(s), rebuild, run `pytest`, record **pass** / **fail**, and restore the source from backup (git is not modified).

### Discover functions (requires `ctags`)

```bash
cd PesudoScope

python3 pseudoscope.py discover \
  --workdir ultrajson \
  --out ultrajson/.pseudoscope/functions_discovered.json
```

`--source-root` is optional: if omitted, paths like `src/` or `libCacheSim/libCacheSim/` are inferred from `--workdir`.
```

Skips directories named `test`, `tests`, `deps`, `cctest`, etc.

### Run sweep

```bash
cd PesudoScope

python3 pseudoscope.py sweep \
  --workdir ultrajson \
  --build-command 'pip install -e ".[dev]"' \
  --test-command pytest \
  --out ultrajson/.pseudoscope/sweep_results.csv
```

Each mutant is one CSV row: `file,function,mutant_id,result`.

After the sweep, the tool prints an **aligned table** and a **summary** to the terminal, and writes `*_table.txt` next to the CSV (same folder, same basename).

**PI** = percentage of **functions** that passed. Each function gets up to two default-return mutants; **if either run fails, the function is fail**. Pass only when every mutant for that function passed.

```
File | Function | Mutant | Result
---------------------------------
python/ujson.c | object_is_decimal_type | zero | FAIL
...

=== Summary ===
Functions:  85 total  |  2 pass  |  83 fail
PI: 2.4%  — pass only if all default-return mutants passed; any fail counts as fail
```

Progress (`[3/85] ...`) goes to stderr; the table prints once at the end. Use `--live-rows` to echo each row as it completes.

Use `--max-functions 5` to try a small run first. Backups are stored under `ultrajson/.pseudoscope/backups/`.

### Default-return mutants (two per type, except `void`)

| Category | mutant_id | Body |
|----------|-----------|------|
| `void` | `return` | `return;` |
| `bool` | `false` / `true` | `return false;` / `return true;` |
| `int` | `zero` / `one` | `return 0;` / `return 1;` |
| `float` | `zero` / `one` | `return 0.0;` / `return 1.0;` |
| `pyobject` | `null` / `none` | `return NULL;` / `Py_INCREF(Py_None); return Py_None;` |
| `char_ptr` | `null` / `empty` | `return NULL;` / `return "";` |
| `void_ptr` | `null` / `sentinel` | `return NULL;` / `return (void*)1;` |

Build failures are recorded as **fail** (same as test failures).

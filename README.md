# PseudoScope

Research on **pseudo-tested** C/C++ functions in [UltraJSON](https://github.com/ultrajson/ultrajson), inspired by [PSEUDOSWEEP](https://github.com/mdecourse/PSEUDOSWEEP).

The idea: replace a function body with a default return (e.g. `return 0;`), rebuild, and run tests. If tests still pass, the function may be **pseudo-tested** (the suite does not detect the mutation).

## Repository layout

```
PesudoScope/
├── README.md
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

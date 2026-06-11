# PseudoScope

Research prototype for detecting **pseudo-tested** C/C++ code, inspired by [PSEUDOSWEEP](https://github.com/mdecourse/PSEUDOSWEEP).

Replace a function body with minimal `return` statements → **rebuild** → run tests. If tests still pass, the function may not be adequately tested against that mutation.

- `pseudoscope/` — analysis CLI
- `ultrajson/` — sample target ([UltraJSON](https://github.com/ultrajson/ultrajson))

Source files are always restored after each mutation test.

---

## Requirements

- Python 3.10+
- macOS or Linux (C toolchain)
- Target project: build step + test runner (e.g. `pytest`); `--test-command` runs with `shell=True`

---

## Setup

```bash
git clone git@github.com:IsseiHasegawa/PesudoScope.git
cd PesudoScope
git submodule update --init --recursive   # if ultrajson/ is empty

# Target (ultrajson) — ensure all tests pass
cd ultrajson
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && pytest
cd ..

# PseudoScope — separate venv at repo root
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

**Note:** Run `python -m pseudoscope` from the **repo root** (`PesudoScope/`), not from `ultrajson/`. Deactivate `ultrajson/.venv` before using the PseudoScope venv.

For C extensions, **include a build** in `--test-command` (e.g. `pip install -e . && pytest`). `pytest` alone may load a stale `.so` and produce false PASS results.

---

## Usage

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--project-root-source-dir` | yes | — | Project root (cwd for tests) |
| `--test-command` | yes | — | Shell command to run tests |
| `--file` | * | — | Path relative to project root (*required to run analysis) |
| `--function` | no | — | Function name; omit for **file sweep** (`.c` / `.cpp`) |
| `--output-dir` | no | project root | Directory for JSON output |
| `--output-file` | no | `pseudoscope-results.json` | Output file name |
| `--timeout` | no | `60` | Timeout per test run (seconds) |
| `--mode` / `--lang` | no | — | Reserved (unused) |

Output paths are resolved under `--project-root-source-dir`. Use `--output-file foo.json` → `ultrajson/foo.json`, not `ultrajson/ultrajson/...` in the file name.

If the **baseline** test (one run before mutations) does not exit 0, all functions are skipped as `baseline_failed`. Confirm `pytest` passes in the target directory first.

### Single function

```bash
cd PesudoScope
source .venv/bin/activate

python -m pseudoscope \
  --file src/ujson/python/objToJSON.c \
  --project-root-source-dir ultrajson \
  --test-command "source .venv/bin/activate && pip install -e . && pytest" \
  --function Tuple_iterNext \
  --output-file pseudoscope-results.json \
  --timeout 120
```

### File sweep (all functions in one file)

Omit `--function`. This can take a long time; try a single function first.

```bash
deactivate

cd /Users/issei/Documents/summer-research/PesudoScope
source .venv/bin/activate

python -m pseudoscope \
  --file src/ujson/python/objToJSON.c \
  --project-root-source-dir ultrajson \
  --test-command "source .venv/bin/activate && pip install -e . && python -m pytest" \
  --output-file pseudoscope-sweep-objToJSON.json \
  --timeout 120
```

### Reading results

| Label | Meaning |
|-------|---------|
| `PASS (PT candidate)` | Tests passed after mutation |
| `FAIL (detected)` | Tests failed after mutation |
| `TIMEOUT` | Test command timed out |

JSON includes `baseline`, `classification`, `mutations`, `table_rows`. File sweep adds `"mode": "file_sweep"` and `functions[]`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `No module named pseudoscope` | Run from repo root; deactivate `ultrajson` venv |
| All functions `Skipped (baseline_failed)` | Fix environment so `pytest` exits 0 (Python version, build in command) |
| All mutations PASS unexpectedly | Add build step to `--test-command` |
| `pytest: command not found` | `source` the target venv inside `--test-command` |

---

## Default return values

PseudoScope infers the return type from the function signature (Tree-sitter when available, otherwise regex) and replaces the body with minimal `return` statements. Categories with two values generate **two mutations** (one per return).

| Category | Matched return types | Replacement body(ies) |
|----------|----------------------|------------------------|
| `void` | `void` | `return;` |
| `bool` | `bool` | `return false;` · `return true;` |
| `integer` | `int`, `short`, `long`, `unsigned *`, `size_t`, `std::size_t`, … | `return 0;` · `return 1;` |
| `float` | `float` (not `double`) | `return 0.0f;` · `return 1.0f;` |
| `double` | `double` | `return 0.0;` · `return 1.0;` |
| `string` | `string`, `std::string` | `return "";` · `return "A";` |
| `char` | `char` | `return '\0';` · `return 'a';` |
| `pointer` | any type containing `*` (e.g. `PyObject *`, `char *`) | `return nullptr;` |
| `fallback` | STL containers (`std::vector`, `std::map`, …), unrecognized types | `return {};` |

**Type inference order** (first match wins): pointer → `void` → `bool` → `string` → STL container → `char` → `float` → `double` → integer → `fallback`.

Implementation: `pseudoscope/mutate.py`.

---

## gcov: which tests execute which C lines

PseudoScope JSON `stdout` lists tests that **fail** on a mutant. **gcov** shows which C lines a test **executes**. Use both when mapping tests to functions (e.g. `List_iterNext`, `Tuple_iterNext` in `objToJSON.c`).

| Tool | Answers |
|------|---------|
| PseudoScope | Did the test suite detect this mutation? |
| gcov | Did this test run this C line? |

### Prerequisites

From `ultrajson/` with its venv active:

```bash
pip install setuptools setuptools-scm pytest
gcov --version   # macOS: Xcode or Homebrew LLVM
```

### 1. Build with coverage flags

A normal `pip install -e .` build is **not** instrumented. Rebuild with coverage before running gcov:

```bash
cd ultrajson
source .venv/bin/activate
rm -rf build cov

CFLAGS="--coverage -O0 -g" \
CXXFLAGS="--coverage -O0 -g" \
LDFLAGS="--coverage" \
python setup.py build_ext --inplace
```

- `-O0` — disable optimization (line-accurate counts)
- `-g` — debug symbols
- `--coverage` — emit profiling hooks

This creates `.gcno` files under `build/temp.*/src/ujson/python/` (not next to the `.c` sources).

### 2. Run tests

Use `python -m pytest` so the correct venv is used:

```bash
# Full suite
python -m pytest

# Single test
python -m pytest tests/test_ujson.py::test_encode_list_conversion -q
python -m pytest 'tests/test_ujson.py::test_dumps[test_input4-[true,false,null]]' -q
```

Each run writes `.gcda` files into the same `build/temp.*/` directory.

### 3. Generate reports

**All C files** (recommended — uses `ultrajson/scripts/coverage.sh`):

```bash
./scripts/coverage.sh
# Output: ultrajson/cov/*.gcov
```

**One source file**:

```bash
gcov --color src/ujson/python/objToJSON.c -o build/temp.*/src/ujson/python
# Output: objToJSON.c.gcov in the current directory
```

On Linux, `coverage.sh` passes `--relative-only` to gcov; on macOS it passes `--color`.

### 4. Read `.gcov` output

```bash
grep "List_iterNext" cov/objToJSON.c.gcov
grep "186:" cov/objToJSON.c.gcov
```

Each line looks like:

```
        5:  186:static int List_iterNext(...)
    #####:  165:static int Tuple_iterNext(...)
        -:  166:{
```

| Prefix | Meaning |
|--------|---------|
| `N:` | Line executed **N** times |
| `#####:` | Line **never** executed |
| `-:` | Non-executable (blank line, declaration, etc.) |

Example mappings for `objToJSON.c`:

| Test | Function | Lines to check |
|------|----------|----------------|
| `test_encode_list_conversion` | `List_iterNext` | `477:` (register), `186:` (body) |
| `test_dumps[test_input4-…]` (tuple) | `Tuple_iterNext` | `489:` (register), `165:` (body) |

### 5. Compare tests in isolation

`.gcda` data **accumulates** across runs. Clear it before each test when comparing coverage:

```bash
rm -f build/temp.*/src/ujson/python/*.gcda
python -m pytest tests/test_ujson.py::test_encode_list_conversion -q
./scripts/coverage.sh
```

### 6. Test × function matrix (CSV)

`ultrajson/scripts/test_function_matrix.py` runs each pytest test in isolation, collects gcov line hits, and writes a CSV (`1` = executed, `0` = not).

**Prerequisite:** coverage build (step 1) and a PseudoScope sweep JSON with function line ranges (default: `pseudoscope-sweep-objToJSON.json`).

```bash
cd ultrajson
source .venv/bin/activate

# Full matrix (380 tests — takes several minutes)
python scripts/test_function_matrix.py -o test-function-matrix.csv

# Quick preview
python scripts/test_function_matrix.py --limit 20 -o matrix-sample.csv

# Single test or subset
python scripts/test_function_matrix.py \
  --tests tests/test_ujson.py::test_encode_list_conversion \
  -o one-test.csv
```

| Option | Description |
|--------|-------------|
| `-o`, `--output` | Output CSV path (default: `test-function-matrix.csv`) |
| `--limit N` | First N collected tests only |
| `--tests PATH` | pytest path or node id prefix (default: `tests`) |
| `--sweep-json FILE` | Function line ranges (default: `pseudoscope-sweep-objToJSON.json`) |
| `--source-file PATH` | C file to analyze (default: `src/ujson/python/objToJSON.c`) |

CSV format: rows = tests, columns = functions, cells = `1` if any line in the function body was executed.

**Note:** If a test body no longer calls ujson (e.g. `pass` only), the matrix correctly shows `0` for all functions.

### gcov troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| All lines show `#####:` / 0% | Extension built without `--coverage` | Re-run `build_ext --inplace` with `CFLAGS`/`LDFLAGS` above; avoid overwriting with plain `pip install -e .` before gcov |
| gcov cannot find data | Wrong `-o` path | Point `-o` at `build/temp.*/src/ujson/python` |
| Mixed results from several tests | Stale `.gcda` | `rm -f build/temp.*/src/ujson/python/*.gcda` before each run |
| gcov broken after PseudoScope | PseudoScope rebuilds without coverage | Re-run the coverage build in step 1 |

After PseudoScope sweeps, rebuild with coverage flags before running gcov again.

---

## References

- [PSEUDOSWEEP](https://github.com/mdecourse/PSEUDOSWEEP) · [UltraJSON](https://github.com/ultrajson/ultrajson)
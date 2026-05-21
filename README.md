# PseudoScope (MVP)

Research prototype for detecting **pseudo-tested** C/C++ functions, inspired by [PSEUDOSWEEP](https://github.com/mdecourse/PSEUDOSWEEP).

For each function, PseudoScope replaces the body with default return values (e.g. `return 0;`), rebuilds the project, and runs tests. If tests still pass, the function may be **pseudo-tested** (tests do not detect the mutation).

## Architecture

```
discover  →  functions.json   (file, name, line, return_type, body range)
   ↓
sweep     →  for each function:
               backup source
               for each mutant:
                 patch body → build → test → record
               restore source (always)
   ↓
results.json  →  per-function classification
```

| Layer | Module in `pseudoscope.py` | Replaceable later with |
|-------|---------------------------|-------------------------|
| Discovery | regex + brace matching | Tree-sitter / Clang LibTooling |
| Mutants | return-type → replacement table | richer type analysis |
| Mutation | text patch + backup/restore | Clang rewriter |
| Runner | `subprocess` + timeout | CI integration |
| Report | JSON | DB / HTML dashboard |

## Requirements

- Python 3.10+
- C++ compiler (`c++` / `g++`)
- `make` (for example projects)

## Quick start — `Test/src/hello.cpp`

```bash
cd PesudoScope

# 1. Discover functions in hello.cpp
python3 pseudoscope.py discover \
  --source Test/src \
  --files hello.cpp \
  --out Test/.pseudoscope/functions.json

# 2. Mutation sweep (use -B to force rebuild after each patch)
python3 pseudoscope.py sweep \
  --functions Test/.pseudoscope/functions.json \
  --workdir Test \
  --source-root Test/src \
  --build-command "make -B test_hello" \
  --test-command "./test_hello" \
  --out Test/.pseudoscope/results.json \
  --timeout 60
```

Expected result for the current `hello.cpp` tests: all four functions are **`killed`** (tests detect every mutant).

### Test result table

```bash
# ターミナル向け（パイプ区切り）
python3 pseudoscope.py report \
  --results Test/.pseudoscope/results.json \
  --out Test/.pseudoscope/results.table.txt

# Excel / Google スプレッドシート向け（CSV・推奨）
python3 pseudoscope.py report \
  --results Test/.pseudoscope/results.json \
  --format csv \
  --out Test/.pseudoscope/results.csv
```

Output columns:

```
file name | Function name | Test name | error message
```

Example (`Test/.pseudoscope/results.table.txt`):

```
hello.cpp | hello | hello() output | FAIL: hello() output expected "Hello world\n", got ""
hello.cpp | TwoSum | TwoSum(3, 4) | FAIL: TwoSum(3, 4) expected 7, got 0
```

Use `--include-passed` to add rows for mutants where tests still passed (`pseudo_tested`).

## Example project — `examples/simple_cpp/`

Contains `unused_helper()` which is **not** called by tests → should be **`pseudo_tested`**.

```bash
cd examples/simple_cpp
make test

cd ../..
python3 pseudoscope.py discover \
  --source examples/simple_cpp/src \
  --out examples/simple_cpp/.pseudoscope/functions.json

python3 pseudoscope.py sweep \
  --functions examples/simple_cpp/.pseudoscope/functions.json \
  --workdir examples/simple_cpp \
  --source-root examples/simple_cpp/src \
  --build-command "make -B test_math" \
  --test-command "./test_math" \
  --out examples/simple_cpp/.pseudoscope/results.json
```

See [examples/simple_cpp/.pseudoscope/example_results.json](examples/simple_cpp/.pseudoscope/example_results.json) for sample output.

## Classifications

| `final_classification` | Meaning |
|------------------------|---------|
| `pseudo_tested` | All mutants survived (tests still pass) |
| `killed` | All mutants detected by tests |
| `partially_detected` | Some survived, some killed |
| `build_failed` | Mutant does not compile |
| `unsupported` | Return type not handled in MVP |

Per-mutant `classification`: `survived`, `killed`, or `build_failed`.

## Supported return types (MVP)

| Type | Mutants |
|------|---------|
| `void` | `return;` |
| `bool` | `return false;`, `return true;` |
| `int`, `long`, `short`, `size_t`, … | `return 0;`, `return 1;` |
| `float`, `double` | `return 0.0;`, `return 1.0;` |
| pointers | `return nullptr;` |
| `std::string` | `return "";`, `return "A";` |

Skipped: constructors, destructors, operators, templates, lambdas, macros, header-only functions, complex return types.

## Safety

- Original files are copied to `.pseudoscope/backups/` before mutation.
- Sources are **always restored** after each function, even on build/test failure or timeout.

## Extending

- Statement-level mutation: add a new layer beside `apply_mutation()`.
- Clang LibTooling: replace `discover_functions()` and `apply_mutation()`.
- CMake projects: use `--build-command "cmake --build build"` and `--test-command "ctest --test-dir build --output-on-failure"`.

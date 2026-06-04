# PseudoScope benchmark target

Small C/C++ extension + pytest project for checking **file sweep** and related PseudoScope behavior. Same build style as `ultrajson/` (`pip install -e .` before tests).

## Setup

From this directory (`benchmark/`):

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -e ".[test]"
pytest -q
```

All tests should pass before running PseudoScope.

## Layout

| Path | Role |
|------|------|
| `src/benchmark_ops.c` | 12 C functions (`static`, `void`, `int`, `bool`, `double`, pointer) |
| `src/benchmark_cpp.cpp` | 4 C++ functions (`extern "C"`) |
| `src/bench_module.c` | Python C API wrapper |
| `tests/test_benchmark.py` | Partial coverage on purpose |

## Run PseudoScope

From **`PesudoScope/`** (repo root), with the **PseudoScope** venv active (`pip install -e .` there). Do **not** use `benchmark/.venv` for `python -m pseudoscope`.

```bash
cd /path/to/PesudoScope
source .venv/bin/activate
```

### Single function

```bash
python -m pseudoscope \
  --project-root-source-dir benchmark \
  --file src/benchmark_ops.c \
  --function bench_add \
  --test-command "source .venv/bin/activate && pip install -e . && pytest" \
  --output-file pseudoscope-results.json \
  --timeout 120
```

### File sweep — `benchmark_ops.c`

```bash
python -m pseudoscope \
  --project-root-source-dir benchmark \
  --file src/benchmark_ops.c \
  --test-command "source .venv/bin/activate && pip install -e . && pytest" \
  --output-file pseudoscope-sweep-ops.json \
  --timeout 120
```

### File sweep — `benchmark_cpp.cpp`

```bash
python -m pseudoscope \
  --project-root-source-dir benchmark \
  --file src/benchmark_cpp.cpp \
  --test-command "source .venv/bin/activate && pip install -e . && pytest" \
  --output-file pseudoscope-sweep-cpp.json \
  --timeout 120
```

`--output-dir` and `--output-file` are relative to `--project-root-source-dir` (`benchmark/`).

## Expected summary (file sweep)

Use these to sanity-check the table footer (`PASS (PT candidate)` / Pass rate). Exact mutation rows can vary; **function-level** labels should match when the benchmark is unchanged.

### `src/benchmark_ops.c` (12 functions)

| Function | Expected `classification.label` |
|----------|----------------------------------|
| `static_doubled` | `not_pseudo_tested` |
| `static_orphan` | `pseudo_tested_candidate` |
| `bench_add` | `not_pseudo_tested` |
| `bench_mystery_add` | `pseudo_tested_candidate` |
| `bench_is_even` | `not_pseudo_tested` |
| `bench_bool_unused` | `pseudo_tested_candidate` |
| `bench_noop_void` | `pseudo_tested_candidate` |
| `bench_void_smoke` | `pseudo_tested_candidate` |
| `bench_weak_not_zero` | `partially_tested` |
| `bench_alloc_id` | `not_pseudo_tested` |
| `bench_mean_two_doubles` | `not_pseudo_tested` |
| `bench_double_secret` | `pseudo_tested_candidate` |

**Footer target:** Analyzed **12**, PASS **6**, Pass rate **50.0%**

### `src/benchmark_cpp.cpp` (4 functions)

| Function | Expected `classification.label` |
|----------|----------------------------------|
| `cpp_add` | `not_pseudo_tested` |
| `cpp_mystery` | `pseudo_tested_candidate` |
| `cpp_weak_not_zero` | `partially_tested` |
| `cpp_string_size` | `not_pseudo_tested` |

**Footer target:** Analyzed **4**, PASS **1**, Pass rate **25.0%**

## Notes

- Rebuild is required: `--test-command` must include `pip install -e .` (or equivalent).
- `static` functions appear in the sweep even without Python bindings.
- A second `.c` file can be added later for folder-wide sweep experiments.

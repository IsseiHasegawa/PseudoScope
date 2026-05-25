# PseudoScope

Research prototype for detecting **pseudo-tested** C/C++ code, inspired by [PSEUDOSWEEP](https://github.com/mdecourse/PSEUDOSWEEP).

**Idea:** replace a function body with a minimal default return, rebuild, and run the project’s test suite. If tests still pass, the function may be **pseudo-tested** (the suite did not detect the mutation).

**Current implementation:** Step 1 only — parse, normalize, and validate CLI input. Later steps will read the target file, locate and mutate the function body, run tests, restore the source, and write JSON results.

## Repository layout

```
PesudoScope/
├── README.md
├── pseudoscope/              # Python package (Step 1 CLI)
│   ├── cli.py
│   ├── models.py
│   ├── validation.py
│   └── pipeline.py           # Planned steps (documentation)
└── ultrajson/                # Primary study target (nested git repo)
    ├── src/
    ├── tests/
    └── baseline_test_result.txt
```

## Requirements

- Python 3.10+
- For **ultrajson**: C/C++ toolchain, `pip`, and `pytest`

## PseudoScope CLI (Step 1)

Run from the `PesudoScope/` directory:

```bash
python3 -m pseudoscope --help
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--project-root` | yes | — | Project root; tests will run from here |
| `--file` | yes | — | Source path **relative** to project root |
| `--function` | yes | — | Function or method name in that file |
| `--test-command` | yes | — | Shell command for tests (validated, not executed yet) |
| `--output` | no | `pseudoscope-results.json` | Planned JSON path (relative paths resolve under project root) |
| `--timeout` | no | `60` | Planned test timeout in seconds |

### Example (ultrajson)

```bash
cd PesudoScope

python3 -m pseudoscope \
  --project-root ultrajson \
  --file src/ujson/python/objToJSON.c \
  --function Tuple_iterNext \
  --test-command "pytest"
```

On success:

```
PseudoScope configuration loaded successfully.

Project root: .../ultrajson
Target file: .../ultrajson/src/ujson/python/objToJSON.c
Function: Tuple_iterNext
Test command: pytest
Output file: .../ultrajson/pseudoscope-results.json
Timeout: 60 seconds
```

### What Step 1 does not do

- Does not read or modify source file contents
- Does not run `--test-command`
- Does not create the output JSON file

See `pseudoscope/pipeline.py` for the planned full workflow.

## UltraJSON — build and test

Primary target: [UltraJSON](https://github.com/ultrajson/ultrajson) in `ultrajson/`.

`ujson` is a C extension (`ujson.cpython-*.so`). **After every change to C/C++ under `src/`, rebuild before running tests.** `pytest` alone reuses the last built `.so`.

| Changed files | Rebuild needed? |
|---------------|-----------------|
| `src/**/*.c`, `src/**/*.cc` | Yes — `pip install -e .` |
| `tests/*.py` only | No — `pytest` is enough |

### First-time setup

```bash
cd ultrajson

python3 -m venv .venv
source .venv/bin/activate

pip install -e .
pytest
```

### Day-to-day (after editing C)

```bash
cd ultrajson
source .venv/bin/activate
pip install -e .
pytest
```

Quick check:

```bash
python3 -c "import ujson; print(ujson.dumps({'ok': 1}))"
```

Passing baseline: `ultrajson/baseline_test_result.txt` (379 tests at time of recording).

## Optional: count functions with ctags

Run inside `ultrajson/` to see how many functions each file defines:

```bash
cd ultrajson

find src \( -name "*.c" -o -name "*.cpp" -o -name "*.cc" -o -name "*.cxx" \) -type f -print0 |
while IFS= read -r -d '' file; do
  count=$(ctags -x --kinds-C=f --kinds-C++=f "$file" 2>/dev/null | wc -l | tr -d ' ')
  printf "%s\t%s\n" "$file" "$count"
done
```

Requires [Universal Ctags](https://github.com/universal-ctags/ctags) on `PATH`.

## Roadmap

| Step | Status | Module (planned) |
|------|--------|------------------|
| Validate CLI input | done | `cli`, `validation` |
| Read target file | planned | `source` |
| Locate function body | planned | `locate` |
| Delete / replace body | planned | `mutate` |
| Run test command | planned | `runner` |
| Restore original file | planned | `mutate` |
| Write JSON results | planned | `results` |

## References

- [PSEUDOSWEEP](https://github.com/mdecourse/PSEUDOSWEEP)
- [UltraJSON](https://github.com/ultrajson/ultrajson)

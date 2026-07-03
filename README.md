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
| `--test-command` | yes | — | Shell command to run tests (fallback when no coverage map applies) |
| `--file` | * | — | Path relative to project root (*required to run analysis) |
| `--function` | no | — | Function name; omit for **file sweep** (`.c` / `.cpp`) |
| `--output-dir` | no | project root | Directory for JSON output |
| `--output-file` | no | `pseudoclang-results.json` | Output file name |
| `--timeout` | no | `60` | Timeout per test run (seconds) |
| `--coverage-map` | no | — | pstrace coverage-map JSON (`pstrace-coverage/1`); path relative to project root |
| `--coverage-map-cmd` | no | — | Shell command to generate `--coverage-map` (sets `$PSEUDOCLANG_COVERAGE_MAP`); requires `--coverage-map` |
| `--refresh-coverage-map` | no | off | Regenerate the coverage map even if the file exists; requires `--coverage-map-cmd` |
| `--test-runner-template` | no | — | Command template with `{selection}` for running a test subset (e.g. `pytest {selection}`) |
| `--assume-coverage-complete` | no | off | Treat functions absent from the map as untested (skip mutants); requires `--coverage-map` |
| `--skip-runner-check` | no | off | Skip preflight rebuild check when `--coverage-map` and `--test-runner-template` are both set |
| `--mode` / `--lang` | no | — | Reserved (unused) |

Output paths are resolved under `--project-root-source-dir`. Use `--output-file foo.json` → `ultrajson/foo.json`, not `ultrajson/ultrajson/...` in the file name.

If the **baseline** test (one run before mutations) does not exit 0, all functions are skipped as `baseline_failed`. Confirm `pytest` passes in the target directory first.

Example 1 (ultrajson):

#1 Clone PseudoScope
```
git clone https://github.com/IsseiHasegawa/PseudoScope.git
```

#2 Clone ultrajson
```
rm -rf ultrajson && git clone https://github.com/ultrajson/ultrajson.git
```

#3 Make venv for ultrajson
```
python3 -m venv ultrajson/.venv
ultrajson/.venv/bin/pip install -e ultrajson
```

#4 Make venv for PseudoClang

```
python3 -m venv PseudoClang/.venv
PseudoClang/.venv/bin/pip install -e PseudoClang
```

#5 Apply PseudoClang to ultrajson (objToJSON.c)
```
cd /Users/issei/Documents/summer-research/PsedoClang
source .venv/bin/activate

python -m pseudoclang \
  --file src/ujson/python/objToJSON.c \
  --project-root-source-dir ultrajson \
  --test-command "source .venv/bin/activate && pip install -e . -q && python -m pytest -q" \
  --test-runner-template "source .venv/bin/activate && pip install -e . -q && python -m pytest -q {selection}" \
  --pstrace-module ujson \
  --pstrace-src-root src/ujson \
  --pstrace-build-cmd "python setup.py build_ext --inplace --force" \
  --pstrace-test-cmd "python -m pytest tests/" \
  --pstrace-python ultrajson/.venv/bin/python \
  --output-file sweep-objToJSON.json
```

#6 Apply PseudoClang to ultrajson (JSONtoObj.c)
```
python -m pseudoclang \
  --file src/ujson/python/JSONtoObj.c \
  --project-root-source-dir ultrajson \
  --test-command "source .venv/bin/activate && pip install -e . -q && python -m pytest -q" \
  --test-runner-template "source .venv/bin/activate && pip install -e . -q && python -m pytest -q {selection}" \
  --pstrace-module ujson \
  --pstrace-src-root src/ujson \
  --pstrace-build-cmd "python setup.py build_ext --inplace --force" \
  --pstrace-test-cmd "python -m pytest tests/" \
  --pstrace-python ultrajson/.venv/bin/python \
  --output-file sweep-JSONtoObj.json
```

Example 2 (Pillow):

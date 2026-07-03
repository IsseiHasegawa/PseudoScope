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
| `--output-dir` | no | PseudoClang's `output/` | Directory for JSON output (relative paths resolve under the project root) |
| `--output-file` | no | `pseudoclang-results.json` | Output file name |
| `--timeout` | no | `60` | Timeout per test run (seconds) |
| `--coverage-map` | no | — | pstrace coverage-map JSON (`pstrace-coverage/1`); path relative to project root |
| `--coverage-map-cmd` | no | — | Shell command to generate `--coverage-map` (sets `$PSEUDOCLANG_COVERAGE_MAP`); requires `--coverage-map` |
| `--refresh-coverage-map` | no | off | Regenerate the coverage map even if the file exists; requires `--coverage-map-cmd` |
| `--test-runner-template` | no | — | Command template with `{selection}` for running a test subset (e.g. `pytest {selection}`) |
| `--assume-coverage-complete` | no | off | Treat functions absent from the map as untested (skip mutants); requires `--coverage-map` |
| `--skip-runner-check` | no | off | Skip preflight rebuild check when `--coverage-map` and `--test-runner-template` are both set |
| `--mode` / `--lang` | no | — | Reserved (unused) |

By default, results are written under PseudoClang's own `output/` directory (and the auto-generated pstrace coverage map under `output/coverage-map.json`), so a run leaves the target project's tree untouched. Pass `--output-dir` to redirect; a relative `--output-dir` resolves under `--project-root-source-dir` (e.g. `--output-dir . --output-file foo.json` → `ultrajson/foo.json`).

If the **baseline** test (one run before mutations) does not exit 0, all functions are skipped as `baseline_failed`. Confirm `pytest` passes in the target directory first.

Example 1 (ultrajson):

#1 Clone PseudoScope
```bash
git clone https://github.com/IsseiHasegawa/PseudoScope.git
```

#2 Clone ultrajson
```bash
rm -rf ultrajson && git clone https://github.com/ultrajson/ultrajson.git
```

#3 Make venv for ultrajson
```bash
python3 -m venv ultrajson/.venv
ultrajson/.venv/bin/pip install -e ultrajson
```

#4 Run tests
```bash
cd /Users/issei/Documents/summer-research/PsedoClang/ultrajson

source .venv/bin/activate
pip install pytest
pip install -e .
python -m pytest -q
```


#5 Make venv for PseudoClang
```bash
python3 -m venv PseudoClang/.venv
PseudoClang/.venv/bin/pip install -e PseudoClang
```

#6 Apply PseudoClang to ultrajson (objToJSON.c)
```bash
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

#7 Apply PseudoClang to ultrajson (JSONtoObj.c)
```bash
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
#1 Make venv for Pillow
```bash
python3 -m venv Pillow/.venv
Pillow/.venv/bin/pip install pybind11
Pillow/.venv/bin/pip install -e "Pillow/.[tests]"
```
#2 Run tests for Pillow
```bash
cd Pillow
../Pillow/.venv/bin/pytest Tests/test_image_filter.py -q
cd ..
```
#3 Make venv for PseudoClang
```bash
python3 -m venv .venv
.venv/bin/pip install -e PseudoClang
```
#4 Apply PseudoClang to Pillow(Filter.c)
```bash
cd /Users/issei/Documents/summer-research/PsedoClang
source .venv/bin/activate

python -m pseudoclang \
  --file src/libImaging/Filter.c \
  --project-root-source-dir Pillow \
  --test-command "pip install -e . -q && pytest Tests/test_image_filter.py Tests/test_imageops_usm.py -q" \
  --test-runner-template "pip install -e . -q && pytest -q {selection}" \
  --pstrace-module PIL._imaging \
  --pstrace-src-root src/libImaging \
  --pstrace-build-cmd "python setup.py build_ext --inplace --force" \
  --pstrace-test-cmd "python -m pytest Tests/test_image_filter.py Tests/test_imageops_usm.py" \
  --pstrace-python Pillow/.venv/bin/python \
  --coverage-map out.json \
  --output-file pillow-filter-sweep.json \
  --timeout 120
```

cd /Users/issei/Documents/summer-research/PsedoClang/Pillow
source .venv/bin/activate   

python setup.py build_ext --inplace --force

pytest Tests/test_image_filter.py -q
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

For C extensions, **include a build** in `--test-command` (e.g. `pip install -e . && pytest`). `pytest` alone may load a stale `.so` and produce false PASS results. PseudoClang now enforces this at runtime: a preflight injects a compile error and rejects a `--test-command` (or `--test-runner-template`) that still passes, i.e. one that does not rebuild the target. Pass `--skip-runner-check` to bypass.

---

## Usage

### Stages (sub-commands)

The CLI is split into stages so you can re-run only what changed. After improving your test suite, re-run just `analyze` to re-check the same functions without rebuilding the (expensive) pstrace coverage map.

> **Caveat when reusing a map with `--test-runner-template`:** a reused map only knows the tests captured when it was built. Re-running `analyze` reflects a *strengthened existing* test (same nodeid), but a test you **add** since is not in the map, so it never runs against a mutant and a genuinely-fixed function can still be reported pseudo-tested. After **adding** tests, rebuild the map (`coverage-map` / `--refresh-coverage-map`). Pass `--test-list-cmd` (a command that prints the current test nodeids) to be warned at runtime about tests the map is missing.

| Sub-command | Does | Typical use |
|-------------|------|-------------|
| `run` (default) | Full pipeline: build the coverage map if needed, then analyze | First run, or when nothing is cached |
| `coverage-map` | Only (re)builds the pstrace coverage map, then exits | Regenerate the map after the source under test changes |
| `analyze` | Only runs mutation analysis, reusing an existing map (never rebuilds it) | Re-check after **strengthening existing** tests; rebuild the map after **adding** tests (see caveat above); skips pstrace |
| `restore` | Undo any mutation a crashed run left in the target project | Recover after a hard crash (`kill -9`, power loss) |
| `snapshots` | List the retained pre-mutation recovery points (source history) | See past run states; find a snapshot to roll back to |

```bash
# Build the map once (pstrace)
python -m pseudoclang coverage-map \
  --project-root-source-dir ultrajson \
  --coverage-map output/coverage-map.json \
  --pstrace-module ujson --pstrace-src-root src/ujson \
  --pstrace-build-cmd "pip install -e ." --pstrace-test-cmd "python -m pytest"

# Improve tests, then re-check WITHOUT rebuilding the map.
# --test-command must rebuild the extension (a stale .so makes every function
# look pseudo-tested, and the preflight will reject a command that skips it).
# --test-list-cmd lets PseudoClang warn if you ADDED tests the reused map lacks.
python -m pseudoclang analyze \
  --project-root-source-dir ultrajson \
  --file src/ujson/python/objToJSON.c --function objToJSON \
  --test-command "pip install -e . -q && pytest" \
  --coverage-map output/coverage-map.json \
  --test-runner-template "pip install -e . -q && pytest {selection}" \
  --test-list-cmd "python -m pytest --collect-only -q | grep '::'"
```

The sub-command is optional: omitting it (the plain `python -m pseudoclang --project-root-source-dir ...` form below) runs `run`, so existing commands keep working.

#### Crash recovery (`restore`)

While analyzing, PseudoClang temporarily rewrites the target's source (inserts a mutant), runs the tests, then restores the original. Normal completion, errors, and Ctrl-C always restore it. A **hard** crash (`kill -9`, OOM, power loss) is the one case the in-process restore cannot cover, so before each mutation the original bytes are saved under PseudoClang's own `output/backups/` (never in the target project). If a crash ever leaves a file mutated, put the target back with:

```bash
python -m pseudoclang restore            # restore everything left mutated
python -m pseudoclang restore --dry-run  # preview what would be restored
```

`restore` only reverts files still in the exact mutated state it left them in; a file you edited (or deleted) since is skipped unless you pass `--force`. A clean run leaves nothing to restore. Set `PSEUDOCLANG_BACKUPS_DIR` to relocate the backup store (or `--backups-dir` on `restore`).

#### Recovery-point history (snapshots)

The crash backup above is cleared as soon as its source is restored, so it cannot take you back to how a file looked before an *earlier* run. For that, each `run`/`analyze` saves the pristine source as a numbered **recovery point** under `output/snapshots/` before mutating it. The most recent `--max-snapshots` (default 5) are kept; older ones are deleted automatically, so the history stays bounded. Identical content is not re-snapshotted, so re-running without editing does not fill the history with copies.

```bash
python -m pseudoclang snapshots              # list retained recovery points
python -m pseudoclang restore --snapshot 3   # roll those files back to snapshot 3
python -m pseudoclang restore --snapshot 3 --dry-run   # preview first
```

Unlike crash `restore`, `restore --snapshot N` is an explicit rollback: it overwrites the current on-disk content with the snapshot's (a file already matching is left untouched). Set `--max-snapshots 0` (or `PSEUDOCLANG_MAX_SNAPSHOTS=0`) to disable the history; `PSEUDOCLANG_SNAPSHOTS_DIR` (or `--snapshots-dir`) relocates the store.

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
| `--test-list-cmd` | no | — | Shell command printing the current test nodeids (one per line, map format), e.g. `pytest --collect-only -q \| grep '::'`. Warns when a reused map is missing current tests; requires `--coverage-map` |
| `--skip-runner-check` | no | off | Skip the preflight rebuild checks (both `--test-command` and `--test-runner-template` must rebuild the target; a command that skips the build tests a stale binary and mislabels every function pseudo-tested) |
| `--max-snapshots` | no | `5` (or `$PSEUDOCLANG_MAX_SNAPSHOTS`) | Recovery points to keep as history; `0` disables it. List with `snapshots`, roll back with `restore --snapshot N` |
| `-v` / `--verbose` | no | off | More detail (repeatable). `-v`: per-function plan + each mutant's exit code/runtime. `-vv`: also each mutant's exact command and the tail of its captured stdout/stderr |
| `-q` / `--quiet` | no | off | Suppress progress narration; print only errors and the final result summary. Overrides `-v` |
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
  --test-list-cmd "python -m pytest --collect-only -q | grep '::'" \
  --pstrace-module ujson \
  --pstrace-src-root src/ujson \
  --pstrace-build-cmd "pip install -e ." \
  --pstrace-test-cmd "python -m pytest tests/" \
  --pstrace-python ultrajson/.venv/bin/python \
  --output-file ultrajson-objToJSON-sweep.json

  
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

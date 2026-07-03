#!/usr/bin/env bash
#
# Generate a pstrace coverage-map JSON (pstrace-coverage/1) for ujson, for
# consumption by PseudoClang's --coverage-map. Intended to be invoked through
# PseudoClang's --coverage-map-cmd, which sets $PSEUDOCLANG_COVERAGE_MAP to the
# output path; it can also be run standalone with the output path as $1.
#
# Mirrors pstrace/README.md (Build / Run / Coverage map) exactly. ujson sources
# are never edited; transient trace artifacts (raw TSV, passing-tests sidecar)
# go to a temp dir, not into the ujson tree. The only ujson change is the
# compiled extension, which is rebuilt un-instrumented at the end (and which
# PseudoClang rebuilds per mutant anyway).
#
# Usage:
#   PSEUDOCLANG_COVERAGE_MAP=/abs/coverage.json bash ujson-pstrace.sh
#   bash ujson-pstrace.sh /abs/coverage.json

set -euo pipefail

# -- locate sibling repos relative to this script (PsedoClang/<repo>/...) -------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"   # PsedoClang/
UJSON="$ROOT/ultrajson"
PSTRACE="$ROOT/pstrace"
CFC_REPO="$ROOT/count-function-call"
CFC_INCLUDE="$CFC_REPO/include"
PY="$UJSON/.venv/bin/python"

# -- resolve output path -------------------------------------------------------
OUT="${PSEUDOCLANG_COVERAGE_MAP:-${1:-}}"
if [ -z "$OUT" ]; then
  echo "ujson-pstrace: set \$PSEUDOCLANG_COVERAGE_MAP or pass an output path" >&2
  exit 2
fi
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac   # absolutize

for p in "$UJSON" "$PSTRACE" "$CFC_REPO" "$CFC_INCLUDE" "$PY"; do
  [ -e "$p" ] || { echo "ujson-pstrace: missing dependency: $p" >&2; exit 2; }
done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cd "$UJSON"

echo "ujson-pstrace: [1/4] instrumented build (-finstrument-functions)"
PYTHONPATH="$PSTRACE" "$PY" -m pstrace.build \
  --target ujson --include "$CFC_INCLUDE" \
  -- setup.py build_ext --inplace --force

echo "ujson-pstrace: [2/4] traced pytest run (per-test native trace)"
# Some tests may fail; the report keeps only passing tests via the sidecar, so a
# non-zero pytest exit is tolerated as long as the raw trace is produced.
PYTHONPATH="$PSTRACE" \
PSTRACE_MODULE=ujson \
PSTRACE_OUTPUT="$WORK/pstrace_raw.tsv" \
PSTRACE_TESTS="$WORK/pstrace_tests.json" \
  "$PY" -m pytest tests/ -p pstrace.plugin || true
[ -s "$WORK/pstrace_raw.tsv" ] || {
  echo "ujson-pstrace: no raw trace produced ($WORK/pstrace_raw.tsv)" >&2; exit 1; }

echo "ujson-pstrace: [3/4] symbolize -> coverage-map JSON -> $OUT"
mkdir -p "$(dirname "$OUT")"
PYTHONPATH="$PSTRACE" "$PY" -m pstrace.report \
  --raw "$WORK/pstrace_raw.tsv" --src-root src/ujson \
  --project-root "$UJSON" --tests "$WORK/pstrace_tests.json" \
  --coverage-json "$OUT"

echo "ujson-pstrace: [4/4] restore the normal build PseudoClang tests against"
# Reproduce the project's normal build (objToJSON.c #includes native_call_counter.h,
# which setup.py wires in only under NCC_ENABLE). This is the same build the
# PseudoClang --test-command uses, so the tree is left clean and consistent.
NCC_ENABLE=1 NCC_REPO="$CFC_REPO" "$PY" setup.py build_ext --inplace --force >/dev/null

echo "ujson-pstrace: done -> $OUT"

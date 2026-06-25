"""CLI: raw address TSV -> per-test CSV and/or a PseudoClang coverage map.

Examples::

    # per-test CSV (test_id, function, file, line, count)
    pstrace-report --raw pstrace_raw.tsv --src-root src/ujson --out per_test.csv

    # coverage map JSON (function -> tests) for PseudoClang
    pstrace-report --raw pstrace_raw.tsv --src-root src/ujson \
        --project-root . --tests pstrace_tests.json --coverage-json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from pstrace.symbolize import (
    build_coverage_map,
    build_table,
    keep_basenames,
    parse_raw,
    write_coverage_json,
    write_csv,
)


def _load_passing(path: str | None) -> set[str] | None:
    """Load the passing-nodeids sidecar; None when absent/unreadable."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return {str(n) for n in data}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", required=True, help="raw TSV written by the C hook")
    ap.add_argument("--out", help="per-test CSV output path")
    ap.add_argument(
        "--coverage-json",
        help="emit the PseudoClang coverage-map JSON (pstrace-coverage/1) to this path",
    )
    ap.add_argument(
        "--project-root",
        help="target project root; coverage keys are made relative to it "
        "(REQUIRED with --coverage-json). Same value the consumer passes as "
        "PseudoClang's --project-root-source-dir",
    )
    ap.add_argument(
        "--tests",
        default="pstrace_tests.json",
        help="passing-nodeids sidecar from the plugin (default: pstrace_tests.json). "
        "If missing, the failed-test filter is skipped",
    )
    ap.add_argument(
        "--src-root",
        help="keep only functions whose definition file lives under this "
        "source tree (deps/double-conversion subtrees are excluded)",
    )
    ap.add_argument(
        "--keep-file",
        action="append",
        default=[],
        metavar="BASENAME",
        help="additional source basename to keep (repeatable)",
    )
    ap.add_argument(
        "--no-filter",
        action="store_true",
        help="keep every resolved function (no source filtering)",
    )
    ap.add_argument(
        "--symbolizer",
        choices=["atos", "addr2line"],
        default=None,
        help="override symbolizer (default: atos on macOS, addr2line elsewhere)",
    )
    args = ap.parse_args(argv)

    if not args.out and not args.coverage_json:
        ap.error("nothing to do: pass --out (CSV) and/or --coverage-json")
    if args.coverage_json and not args.project_root:
        ap.error("--coverage-json requires --project-root")

    keep: set[str] | None
    if args.no_filter:
        keep = None
    else:
        keep = set(args.keep_file)
        if args.src_root:
            keep |= keep_basenames(args.src_root)
        if not keep:
            ap.error(
                "no keep-set: pass --src-root and/or --keep-file, or --no-filter"
            )

    raw = parse_raw(args.raw)

    if args.out:
        rows = build_table(raw, keep_files=keep, tool=args.symbolizer)
        write_csv(rows, args.out)
        tests = {r.test_id for r in rows}
        funcs = {r.function for r in rows}
        print(
            f"pstrace: {len(rows)} (test, function) rows | "
            f"{len(tests)} tests | {len(funcs)} functions -> {args.out}",
            file=sys.stderr,
        )

    if args.coverage_json:
        passing = _load_passing(args.tests)
        if passing is None:
            print(
                f"pstrace: WARNING no passing-tests sidecar at {args.tests!r}; "
                "failed-test filtering is DISABLED and 'tests' only reflects "
                "tests that hit instrumented code",
                file=sys.stderr,
            )
        coverage, tests_list = build_coverage_map(
            raw,
            project_root=args.project_root,
            src_root=args.src_root,
            keep_files=keep,
            passing_nodeids=passing,
            tool=args.symbolizer,
        )
        images = sorted({os.path.basename(r.image) for r in raw if r.image})
        image = images[0] if len(images) == 1 else images
        write_coverage_json(
            coverage,
            tests_list,
            project_root=args.project_root,
            image=image,
            out_path=args.coverage_json,
        )
        n_funcs = sum(len(f) for f in coverage.values())
        print(
            f"pstrace: {len(coverage)} files | {n_funcs} functions | "
            f"{len(tests_list)} passing tests -> {args.coverage_json}",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI: raw address TSV -> per-test (function, file, line, count) CSV.

Example::

    pstrace-report --raw pstrace_raw.tsv --src-root ultrajson/src --out per_test.csv
"""

from __future__ import annotations

import argparse
import sys

from pstrace.symbolize import build_table, keep_basenames, parse_raw, write_csv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", required=True, help="raw TSV written by the C hook")
    ap.add_argument("--out", required=True, help="output CSV path")
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
    rows = build_table(raw, keep_files=keep, tool=args.symbolizer)
    write_csv(rows, args.out)

    tests = {r.test_id for r in rows}
    funcs = {r.function for r in rows}
    print(
        f"pstrace: {len(rows)} (test, function) rows | "
        f"{len(tests)} tests | {len(funcs)} functions -> {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

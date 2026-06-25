"""Turn the raw address table into a per-test (function, file, line) table.

The C hook writes raw rows ``test_id, image, offset, dladdr_sym, count`` where
``offset`` is the function's offset from its image base (ASLR-invariant). Here
we batch-resolve each ``(image, offset)`` to ``function`` + ``file:line`` with a
platform symbolizer and keep only functions defined in the target's own sources
(dropping CPython-header inlines and bundled deps such as double-conversion).
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePath

from pstrace import __version__

# Source extensions considered "the target's own code" when building the
# keep-set from a source root.
SOURCE_SUFFIXES = {".c", ".cc", ".cxx", ".cpp", ".h", ".hpp", ".hh"}
# Path components whose subtrees are treated as third-party (never kept).
DEFAULT_EXCLUDE_DIRS = {"deps", "double-conversion", "third_party", "vendor"}
# Synthetic nodeid the C hook records for calls made before any test (module
# import / collection). It is a legitimate member of a coverage list but never
# a selectable test, so it is exempt from the passing-test filter and excluded
# from the top-level ``tests`` array.
STARTUP = "(startup)"
# Schema identifier embedded in the coverage-map JSON.
COVERAGE_SCHEMA = "pstrace-coverage/1"


@dataclass(frozen=True)
class RawRow:
    test_id: str
    image: str
    offset: int
    dladdr_sym: str
    count: int


@dataclass(frozen=True)
class Sym:
    function: str | None
    file: str | None
    line: int | None


def parse_raw(path: str | Path) -> list[RawRow]:
    rows: list[RawRow] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t", quotechar='"')
        for r in reader:
            try:
                offset = int(r["offset"], 16)
                count = int(r["count"])
            except (KeyError, ValueError):
                continue
            rows.append(
                RawRow(
                    test_id=r.get("test_id", ""),
                    image=r.get("image", ""),
                    offset=offset,
                    dladdr_sym=r.get("dladdr_sym", ""),
                    count=count,
                )
            )
    return rows


# --------------------------------------------------------------------------
# Platform symbolizers: image + list[offset] -> {offset: Sym}
# --------------------------------------------------------------------------

_ATOS_RE = re.compile(r"^(?P<func>.*?) \(in [^)]*\)(?: \((?P<file>[^:()]+):(?P<line>\d+)\))?")


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _symbolize_atos(image: str, offsets: list[int]) -> dict[int, Sym]:
    out: dict[int, Sym] = {}
    for chunk in _chunks(offsets, 1500):
        cmd = ["atos", "-o", image, "-l", "0"] + [hex(o) for o in chunk]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        lines = proc.stdout.splitlines()
        for off, line in zip(chunk, lines):
            m = _ATOS_RE.match(line.strip())
            if not m:
                out[off] = Sym(None, None, None)
                continue
            func = m.group("func") or None
            if func and func.startswith("0x"):
                func = None  # unresolved address, not a real name
            ln = m.group("line")
            out[off] = Sym(func, m.group("file"), int(ln) if ln else None)
    return out


def _symbolize_addr2line(image: str, offsets: list[int]) -> dict[int, Sym]:
    out: dict[int, Sym] = {}
    for chunk in _chunks(offsets, 1500):
        cmd = ["addr2line", "-f", "-C", "-e", image] + [hex(o) for o in chunk]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        lines = proc.stdout.splitlines()
        # addr2line emits two lines per address: function, then file:line
        for i, off in enumerate(chunk):
            func_line = lines[2 * i] if 2 * i < len(lines) else "??"
            loc_line = lines[2 * i + 1] if 2 * i + 1 < len(lines) else "??:0"
            func = None if func_line.strip() in ("??", "") else func_line.strip()
            file_part, _, line_part = loc_line.rpartition(":")
            file = None if file_part in ("??", "") else file_part
            try:
                ln = int(line_part)
            except ValueError:
                ln = None
            # Keep the full path addr2line gives (absolute, or relative to the
            # compile comp_dir). Downstream normalization turns it into a
            # project-root-relative key; the CSV path basenames it itself.
            out[off] = Sym(func, file or None, ln or None)
    return out


def symbolize_image(image: str, offsets: list[int], *, tool: str | None = None) -> dict[int, Sym]:
    if tool is None:
        tool = "atos" if sys.platform == "darwin" else "addr2line"
    if tool == "atos":
        return _symbolize_atos(image, offsets)
    return _symbolize_addr2line(image, offsets)


# --------------------------------------------------------------------------
# Keep-set: which source files count as "the target's own code".
# --------------------------------------------------------------------------


def keep_basenames(src_root: str | Path, exclude_dirs=DEFAULT_EXCLUDE_DIRS) -> set[str]:
    root = Path(src_root)
    keep: set[str] = set()
    for p in root.rglob("*"):
        if p.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if exclude_dirs & set(part.lower() for part in p.parts):
            continue
        keep.add(p.name)
    return keep


# --------------------------------------------------------------------------
# Final table
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TableRow:
    test_id: str
    function: str
    file: str
    line: int | None
    count: int


def build_table(
    raw: list[RawRow],
    *,
    keep_files: set[str] | None,
    tool: str | None = None,
) -> list[TableRow]:
    # Resolve every distinct (image, offset) once.
    by_image: dict[str, set[int]] = defaultdict(set)
    for r in raw:
        by_image[r.image].add(r.offset)
    resolved: dict[tuple[str, int], Sym] = {}
    for image, offs in by_image.items():
        offs_sorted = sorted(offs)
        for off, sym in symbolize_image(image, offs_sorted, tool=tool).items():
            resolved[(image, off)] = sym

    # Join back, filter to the target's own files, aggregate. The symbolizer
    # may now hand back a full path (addr2line); the CSV has always keyed on the
    # basename, so reduce here to keep that output contract on every platform.
    agg: dict[tuple[str, str, str, int | None], int] = defaultdict(int)
    for r in raw:
        sym = resolved.get((r.image, r.offset))
        if sym is None or sym.function is None or sym.file is None:
            continue
        file_base = Path(sym.file).name
        if keep_files is not None and file_base not in keep_files:
            continue
        agg[(r.test_id, sym.function, file_base, sym.line)] += r.count

    rows = [
        TableRow(test_id=k[0], function=k[1], file=k[2], line=k[3], count=v)
        for k, v in agg.items()
    ]
    rows.sort(key=lambda t: (t.test_id, t.file, t.line or 0, t.function))
    return rows


def write_csv(rows: list[TableRow], out_path: str | Path) -> None:
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["test_id", "function", "file", "line", "count"])
        for r in rows:
            w.writerow([r.test_id, r.function, r.file, r.line if r.line is not None else "", r.count])


# --------------------------------------------------------------------------
# Coverage map (pstrace-coverage/1): function -> tests, for PseudoClang.
#
# PseudoClang identifies a function by (project-root-relative source path, bare
# function name) and re-invokes tests by pytest nodeid. The map below is keyed
# so that lookup needs zero transformation on the consumer side.
# --------------------------------------------------------------------------


def build_source_index(
    project_root: str | Path,
    src_root: str | Path | None = None,
    exclude_dirs=DEFAULT_EXCLUDE_DIRS,
) -> dict[str, str]:
    """Map source basename -> project-root-relative POSIX path.

    Used to resolve symbolizer output that is only a bare basename (atos). The
    tree under ``project_root`` is scanned for :data:`SOURCE_SUFFIXES` files;
    third-party subtrees (``deps`` etc.) are skipped to match the keep-set.

    When a basename occurs in more than one directory, the match under
    ``src_root`` wins. If that still leaves more than one candidate the basename
    is genuinely ambiguous and is omitted from the index (callers treat a miss
    as unresolvable and skip the row rather than guess).
    """
    root = Path(project_root).resolve()
    src_root_abs = Path(src_root).resolve() if src_root else None
    excl = {d.lower() for d in exclude_dirs}

    candidates: dict[str, list[Path]] = defaultdict(list)
    for p in root.rglob("*"):
        if p.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if excl & {part.lower() for part in p.parts}:
            continue
        if not p.is_file():
            continue
        candidates[p.name].append(p)

    index: dict[str, str] = {}
    for name, paths in candidates.items():
        chosen: Path | None
        if len(paths) == 1:
            chosen = paths[0]
        else:
            under = [p for p in paths if src_root_abs and p.is_relative_to(src_root_abs)]
            chosen = under[0] if len(under) == 1 else None
        if chosen is not None:
            index[name] = PurePath(os.path.relpath(chosen, root)).as_posix()
    return index


def normalize_source_path(
    symbolized_path: str,
    project_root: str | Path,
    source_index: dict[str, str],
) -> str | None:
    """Turn a symbolizer source path into a project-root-relative POSIX path.

    ``addr2line`` yields a full path (absolute or relative to the compile dir);
    ``atos`` yields a bare basename. Returns ``None`` when a basename cannot be
    resolved unambiguously (caller should warn and skip the row).
    """
    if not symbolized_path:
        return None

    if os.sep in symbolized_path or "/" in symbolized_path:
        if os.path.isabs(symbolized_path):
            rel = os.path.relpath(symbolized_path, str(project_root))
        else:
            # Already relative: treat as project-root-relative as given.
            rel = symbolized_path
        return PurePath(rel).as_posix()

    # Bare basename: resolve via the source index.
    return source_index.get(symbolized_path)


def simplify_function_name(name: str) -> str:
    """Reduce a symbolizer function name to its bare identifier.

    C names pass through unchanged. A demangled C++ name like
    ``ns::Class::method(int)`` loses its parameter signature (everything from
    the first ``(``) and its namespace/class qualifiers (keep the last
    ``::``-separated component) -> ``method``. Overloads collapsing to the same
    key is acceptable; PseudoClang has the same name-only constraint.
    """
    paren = name.find("(")
    if paren != -1:
        name = name[:paren]
    name = name.strip()
    if "::" in name:
        name = name.rsplit("::", 1)[-1]
    return name


def build_coverage_map(
    raw: list[RawRow],
    *,
    project_root: str | Path,
    src_root: str | Path | None,
    keep_files: set[str] | None,
    passing_nodeids: set[str] | None,
    tool: str | None = None,
) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
    """Build the coverage dict and the passing-tests universe.

    Returns ``(coverage, tests)`` where ``coverage[rel_path][func]`` is the
    sorted list of nodeids that exercised ``func`` (plus ``"(startup)"`` when it
    ran at import time). Non-passing nodeids are dropped (``"(startup)"`` is
    always kept); functions whose list ends up empty are omitted.

    If ``passing_nodeids`` is ``None`` no passing filter is applied and ``tests``
    is the sorted distinct real nodeids seen in the raw trace (the caller warns).
    """
    project_root_abs = os.path.abspath(str(project_root))
    source_index = build_source_index(project_root_abs, src_root)

    # Resolve every distinct (image, offset) once.
    by_image: dict[str, set[int]] = defaultdict(set)
    for r in raw:
        by_image[r.image].add(r.offset)
    resolved: dict[tuple[str, int], Sym] = {}
    for image, offs in by_image.items():
        for off, sym in symbolize_image(image, sorted(offs), tool=tool).items():
            resolved[(image, off)] = sym

    coverage: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    unresolved: set[str] = set()
    for r in raw:
        sym = resolved.get((r.image, r.offset))
        if sym is None or sym.function is None or sym.file is None:
            continue
        # Keep-set filter on basename to preserve --src-root/--keep-file
        # semantics, but key the map by the project-relative path.
        basename = Path(sym.file).name
        if keep_files is not None and basename not in keep_files:
            continue
        rel_path = normalize_source_path(sym.file, project_root_abs, source_index)
        if rel_path is None:
            unresolved.add(basename)
            continue
        func = simplify_function_name(sym.function)
        nodeid = r.test_id
        if nodeid == STARTUP or passing_nodeids is None or nodeid in passing_nodeids:
            coverage[rel_path][func].add(nodeid)

    for name in sorted(unresolved):
        print(
            f"pstrace: source basename {name!r} could not be resolved to a "
            "project-relative path (ambiguous or outside project root); "
            "skipping its rows",
            file=sys.stderr,
        )

    # Sort everything and drop empty functions/files.
    out_cov: dict[str, dict[str, list[str]]] = {}
    for rel_path in sorted(coverage):
        funcs: dict[str, list[str]] = {}
        for func in sorted(coverage[rel_path]):
            nodeids = sorted(coverage[rel_path][func])
            if nodeids:
                funcs[func] = nodeids
        if funcs:
            out_cov[rel_path] = funcs

    if passing_nodeids is None:
        universe = {r.test_id for r in raw if r.test_id and r.test_id != STARTUP}
    else:
        universe = {n for n in passing_nodeids if n != STARTUP}
    return out_cov, sorted(universe)


def write_coverage_json(
    coverage: dict[str, dict[str, list[str]]],
    tests: list[str],
    *,
    project_root: str | Path,
    image,
    out_path: str | Path,
) -> None:
    """Assemble the ``meta`` block and write deterministic indent=2 JSON.

    ``image`` is the basename of the instrumented ``.so`` (or a list of
    basenames when the raw trace spans several images). ``created_at`` honors
    ``SOURCE_DATE_EPOCH`` so identical runs can produce byte-identical output.
    """
    meta = {
        "schema": COVERAGE_SCHEMA,
        "project_root": os.path.abspath(str(project_root)),
        "image": image,
        "created_at": _created_at(),
        "pstrace_version": __version__,
    }
    doc = {
        "meta": meta,
        "coverage": {
            path: {func: sorted(coverage[path][func]) for func in sorted(coverage[path])}
            for path in sorted(coverage)
        },
        "tests": sorted(tests),
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def _created_at() -> str:
    """UTC ISO-8601 timestamp (seconds precision), reproducible via
    ``SOURCE_DATE_EPOCH`` when set."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

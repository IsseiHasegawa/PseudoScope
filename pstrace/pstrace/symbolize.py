"""Turn the raw address table into a per-test (function, file, line) table.

The C hook writes raw rows ``test_id, image, offset, dladdr_sym, count`` where
``offset`` is the function's offset from its image base (ASLR-invariant). Here
we batch-resolve each ``(image, offset)`` to ``function`` + ``file:line`` with a
platform symbolizer and keep only functions defined in the target's own sources
(dropping CPython-header inlines and bundled deps such as double-conversion).
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# Source extensions considered "the target's own code" when building the
# keep-set from a source root.
SOURCE_SUFFIXES = {".c", ".cc", ".cxx", ".cpp", ".h", ".hpp", ".hh"}
# Path components whose subtrees are treated as third-party (never kept).
DEFAULT_EXCLUDE_DIRS = {"deps", "double-conversion", "third_party", "vendor"}


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
            out[off] = Sym(func, Path(file).name if file else None, ln or None)
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

    # Join back, filter to the target's own files, aggregate.
    agg: dict[tuple[str, str, str, int | None], int] = defaultdict(int)
    for r in raw:
        sym = resolved.get((r.image, r.offset))
        if sym is None or sym.function is None or sym.file is None:
            continue
        if keep_files is not None and sym.file not in keep_files:
            continue
        agg[(r.test_id, sym.function, sym.file, sym.line)] += r.count

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

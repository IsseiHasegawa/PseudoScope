#!/usr/bin/env python3
"""
PseudoScope — mutation sweep for pseudo-tested C/C++ functions.

For each function under a source tree, replaces the function body with default
return statement(s), rebuilds the project, runs tests, records pass/fail, and
always restores the original source from a backup (no git checkout).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# Filename extensions treated as C/C++ implementation files.
CPP_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}

# Path segments skipped during discovery (test trees, vendored deps, etc.).
DEFAULT_EXCLUDE_DIR_PARTS = frozenset(
    {
        "test",
        "tests",
        "deps",
        "cctest",
        "__pycache__",
        "_build",
        "build",
        "scripts",
        "doc",
        "data",
        ".git",
        ".pseudoscope",
    }
)

# ctags occasionally emits control-flow tokens as bogus "functions".
BOGUS_CTAGS_NAMES = frozenset(
    {
        "if",
        "else",
        "for",
        "while",
        "switch",
        "case",
        "return",
        "goto",
        "break",
        "continue",
    }
)

# Function name prefixes we never mutate (module entry points, etc.).
SKIP_NAME_PREFIXES = ("PyInit_",)

# Return-type tokens that indicate we should not mutate this symbol.
UNSUPPORTED_RETURN_MARKERS = (
    "PyMODINIT_FUNC",
    "BEGIN:",
    "ISITERABLE:",
    "INVALID:",
    "ERROR:",
)


@dataclass
class SweepConfig:
    """Validated settings for a mutation sweep."""

    workdir: Path
    source_root: Path
    build_command: str
    test_command: str
    out_csv: Path
    timeout_seconds: int = 600
    exclude_dir_parts: frozenset[str] = field(
        default_factory=lambda: DEFAULT_EXCLUDE_DIR_PARTS
    )


@dataclass
class FunctionRecord:
    """One discoverable C/C++ function."""

    file: Path  # path relative to source_root
    name: str
    line: int  # 1-based line of the function definition
    return_type: str  # raw spelling from the signature
    category: str  # normalized category for mutant selection
    body_start: int  # 0-based first line inside `{` `}`
    body_end: int  # 0-based last line inside `{` `}` (inclusive)


@dataclass
class MutantSpec:
    """A single default-return variant to inject."""

    mutant_id: str
    lines: tuple[str, ...]  # lines placed inside the function body (no braces)


@dataclass
class SweepRow:
    """One row of sweep output."""

    file: str
    function: str
    mutant_id: str
    result: str  # "pass" or "fail"


@dataclass
class SweepSummary:
    """
    Aggregate statistics after a sweep.

    Each function is tested with up to two default-return mutants. The function
    counts as pass only if every mutant passed; any mutant fail counts as fail.

    PI (Pseudo-tested Index) = function_pass / function_total × 100.
    """

    function_total: int
    function_pass: int  # all mutants for that function passed
    function_fail: int  # at least one mutant failed
    pi_percent: float

    mutant_total: int
    mutant_pass: int
    mutant_fail: int


# -----------------------------------------------------------------------------
# Default-return table (two variants per category, except void)
# -----------------------------------------------------------------------------

MUTANTS_BY_CATEGORY: dict[str, tuple[MutantSpec, ...]] = {
    "void": (
        MutantSpec("return", ("return;",)),
    ),
    "bool": (
        MutantSpec("false", ("return false;",)),
        MutantSpec("true", ("return true;",)),
    ),
    "int": (
        MutantSpec("zero", ("return 0;",)),
        MutantSpec("one", ("return 1;",)),
    ),
    "float": (
        MutantSpec("zero", ("return 0.0;",)),
        MutantSpec("one", ("return 1.0;",)),
    ),
    "pyobject": (
        MutantSpec("null", ("return NULL;",)),
        MutantSpec(
            "none",
            (
                "Py_INCREF(Py_None);",
                "return Py_None;",
            ),
        ),
    ),
    "char_ptr": (
        MutantSpec("null", ("return NULL;",)),
        MutantSpec("empty", ('return "";',)),
    ),
    "void_ptr": (
        MutantSpec("null", ("return NULL;",)),
        MutantSpec("sentinel", ("return (void*)1;",)),
    ),
}


# -----------------------------------------------------------------------------
# Discovery helpers
# -----------------------------------------------------------------------------


def path_should_exclude(path: Path, exclude_parts: frozenset[str]) -> bool:
    """True if any path component matches an excluded directory name."""
    return any(part.lower() in exclude_parts for part in path.parts)


def iter_source_files(source_root: Path, exclude_parts: frozenset[str]) -> Iterator[Path]:
    """Yield C/C++ source files under source_root, respecting exclusions."""
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in CPP_SUFFIXES:
            continue
        if path_should_exclude(path.relative_to(source_root), exclude_parts):
            continue
        yield path


def count_source_files(source_root: Path, exclude_parts: frozenset[str]) -> int:
    """Count C/C++ files under source_root (used to pick an inferred scan root)."""
    return sum(1 for _ in iter_source_files(source_root, exclude_parts))


def infer_source_root(
    workdir: Path,
    explicit: Path | None,
    exclude_parts: frozenset[str],
) -> Path:
    """
    Choose where to scan for C/C++ when ``--source-root`` is omitted.

    Checks common layouts (``src/``, nested ``<project>/<project>/``), then the
    immediate subdirectory with the most ``.c`` / ``.cc`` files, then ``workdir``.
    """
    if explicit is not None:
        return explicit.resolve()

    workdir = workdir.resolve()
    candidates: list[Path] = []

    src = workdir / "src"
    if src.is_dir():
        candidates.append(src)

    # e.g. libCacheSim/libCacheSim/
    nested = workdir / workdir.name
    if nested.is_dir():
        candidates.append(nested)

    best_child: Path | None = None
    best_child_count = 0
    for child in sorted(workdir.iterdir()):
        if not child.is_dir():
            continue
        if path_should_exclude(child.relative_to(workdir), exclude_parts):
            continue
        n = count_source_files(child, exclude_parts)
        if n > best_child_count:
            best_child_count = n
            best_child = child
    if best_child is not None:
        candidates.append(best_child)

    candidates.append(workdir)

    chosen = workdir
    chosen_count = -1
    for candidate in candidates:
        n = count_source_files(candidate, exclude_parts)
        if n > chosen_count:
            chosen_count = n
            chosen = candidate

    return chosen


def run_ctags(file_path: Path) -> list[str]:
    """Run ctags -x on one file; return stdout lines (empty if ctags missing)."""
    cmd = [
        "ctags",
        "-x",
        "--kinds-C=f",
        "--kinds-C++=f",
        str(file_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise SystemExit(
            "ctags not found on PATH. Install Universal Ctags:\n"
            "  https://github.com/universal-ctags/ctags"
        ) from None
    if proc.returncode not in (0, 1):
        # ctags may exit 1 when tags exist; still parse stdout.
        stderr = proc.stderr.strip()
        if stderr and not proc.stdout:
            raise RuntimeError(f"ctags failed for {file_path}: {stderr}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def parse_ctags_line(line: str, file_path: Path) -> tuple[str, int, str] | None:
    """
    Parse one `ctags -x` line.

    Example::
        Dict_iterNext  function  241  path  static int Dict_iterNext(...)

    Returns (name, line, signature_tail) or None if unparsable.
    """
    parts = line.split(None, 3)
    if len(parts) < 4:
        return None
    name, kind, line_s, rest = parts[0], parts[1], parts[2], parts[3]
    if kind != "function":
        return None
    try:
        line_no = int(line_s)
    except ValueError:
        return None
    # rest may start with the same path ctags was run on; strip it if present.
    sig = rest
    path_str = str(file_path)
    if sig.startswith(path_str):
        sig = sig[len(path_str) :].lstrip()
    return name, line_no, sig


def extract_return_type(signature: str, func_name: str) -> str | None:
    """Extract the return-type substring from a ctags signature tail."""
    idx = signature.find(func_name)
    if idx < 0:
        return None
    return signature[:idx].strip()


def _gather_declaration_lines(
    lines: list[str],
    func_line_index: int,
    *,
    max_lookback: int = 12,
) -> list[str]:
    """
    Collect source lines that form the function declaration ending at ``func_line_index``.

    Stops at a prior complete statement (``;`` or ``}``) so the previous function's
    closing brace is not mistaken for part of the return type.
    """
    if func_line_index < 0 or func_line_index >= len(lines):
        return []

    decl: list[str] = [lines[func_line_index]]
    lower_bound = max(0, func_line_index - max_lookback)
    i = func_line_index - 1
    while i >= lower_bound:
        stripped = lines[i].strip()
        if stripped.startswith("#"):
            decl.insert(0, lines[i])
            i -= 1
            continue
        if not stripped:
            i -= 1
            continue
        if stripped.endswith(";") or stripped == "}" or stripped.endswith("}"):
            break
        decl.insert(0, lines[i])
        i -= 1
    return decl


def extract_return_type_from_source(
    lines: list[str],
    line_no_1based: int,
    func_name: str,
    *,
    max_lookback: int = 12,
) -> str | None:
    """
    Read return type from source when ctags only reports ``name(args)``.

    Handles multi-line declarations such as::

        static int
        foo(int x)
    """
    idx = line_no_1based - 1
    if idx < 0 or idx >= len(lines):
        return None
    if func_name not in lines[idx]:
        return None

    decl_lines = _gather_declaration_lines(
        lines, idx, max_lookback=max_lookback
    )
    blob = " ".join(part.strip() for part in decl_lines)
    pos = blob.find(func_name)
    if pos < 0:
        return None
    after = blob[pos + len(func_name) :].lstrip()
    if not after.startswith("("):
        return None

    ret = blob[:pos].strip()
    if not ret:
        return None
    # Reject obvious garbage (e.g. absorbed text from a bad lookback window).
    if "{" in ret or "}" in ret:
        return None
    return ret


def resolve_return_type(
    signature: str,
    func_name: str,
    source_lines: list[str],
    line_no_1based: int,
) -> str | None:
    """Prefer ctags signature; fall back to the definition in source."""
    raw = extract_return_type(signature, func_name)
    if raw:
        return raw
    return extract_return_type_from_source(
        source_lines, line_no_1based, func_name
    )


def normalize_return_type(raw: str) -> str:
    """Collapse whitespace for classification."""
    return re.sub(r"\s+", " ", raw).strip()


def classify_return_type(raw: str) -> str:
    """
    Map a C return type string to a mutant category key.

    Returns ``unsupported`` when no safe default-return pair is defined.
    """
    rt = normalize_return_type(raw)
    upper = rt.upper()

    for marker in UNSUPPORTED_RETURN_MARKERS:
        if marker in upper:
            return "unsupported"

    if rt == "void" or rt.endswith(" void"):
        return "void"

    if re.search(r"\bbool\b", rt):
        return "bool"

    if re.search(r"\b(double|float)\b", rt):
        return "float"

    # Integer-like types (int, JSINT64, size_t, Py_ssize_t, …).
    if re.search(
        r"\b(int|short|long|size_t|ssize_t|uint\d+_t|int\d+_t|JSINT|JSUINT)\b",
        rt,
        re.IGNORECASE,
    ):
        return "int"

    # PyObject*, static PyObject*, JSOBJ (typedef void*).
    if "PyObject" in rt or re.search(r"\bJSOBJ\b", rt, re.IGNORECASE):
        return "pyobject"

    if "char" in rt and "*" in rt:
        return "char_ptr"

    if "*" in rt:
        return "void_ptr"

    return "unsupported"


def should_skip_function(name: str, return_type: str) -> bool:
    """True if this symbol must not be mutated."""
    if name in BOGUS_CTAGS_NAMES:
        return True
    if any(name.startswith(p) for p in SKIP_NAME_PREFIXES):
        return True
    rt = normalize_return_type(return_type)
    for marker in UNSUPPORTED_RETURN_MARKERS:
        if marker in rt:
            return True
    return False


# -----------------------------------------------------------------------------
# Brace matching / body replacement
# -----------------------------------------------------------------------------


def _advance_past_string(source: str, i: int) -> int:
    """Skip a C character or string literal starting at source[i]."""
    quote = source[i]
    i += 1
    while i < len(source):
        ch = source[i]
        if ch == "\\" and i + 1 < len(source):
            i += 2
            continue
        if ch == quote:
            return i + 1
        i += 1
    return len(source)


def find_function_body_span(source: str, func_line_1based: int) -> tuple[int, int] | None:
    """
    Locate the function body as 0-based line indices *inside* the braces.

    Scans from the definition line: finds the parameter list `(...)`, then the
  opening `{`, then matching `}` with naive string/comment handling.
    """
    if func_line_1based < 1:
        return None

    offset = sum(len(line) for line in source.splitlines(keepends=True)[: func_line_1based - 1])
    n = len(source)
    i = offset

    # Find start of parameter list.
    while i < n and source[i] != "(":
        i += 1
    if i >= n:
        return None

    depth = 0
    while i < n:
        ch = source[i]
        if ch in ("'", '"'):
            i = _advance_past_string(source, i)
            continue
        if ch == "/" and i + 1 < n:
            if source[i + 1] == "/":
                while i < n and source[i] != "\n":
                    i += 1
                continue
            if source[i + 1] == "*":
                i += 2
                while i + 1 < n and not (source[i] == "*" and source[i + 1] == "/"):
                    i += 1
                i = min(i + 2, n)
                continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                i += 1
                break
        i += 1

    # Find opening brace of the function body.
    while i < n:
        ch = source[i]
        if ch in ("'", '"'):
            i = _advance_past_string(source, i)
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            while i < n and source[i] != "\n":
                i += 1
            continue
        if ch == "{":
            open_brace_pos = i
            i += 1
            break
        i += 1
    else:
        return None

    depth = 1
    close_brace_pos: int | None = None
    while i < n and depth > 0:
        ch = source[i]
        if ch in ("'", '"'):
            i = _advance_past_string(source, i)
            continue
        if ch == "/" and i + 1 < n:
            if source[i + 1] == "/":
                while i < n and source[i] != "\n":
                    i += 1
                continue
            if source[i + 1] == "*":
                i += 2
                while i + 1 < n and not (source[i] == "*" and source[i + 1] == "/"):
                    i += 1
                i = min(i + 2, n)
                continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                close_brace_pos = i
        i += 1

    if depth != 0 or close_brace_pos is None:
        return None

    def char_to_line(pos: int) -> int:
        return source.count("\n", 0, pos)

    open_line = char_to_line(open_brace_pos)
    close_line = char_to_line(close_brace_pos)

    # Usual layout: opening brace on its own line, body lines, then `}`.
    if open_line < close_line:
        body_start = open_line + 1
        body_end = close_line - 1
        if body_start <= body_end:
            return body_start, body_end
        # Empty body `{` newline `}` — insert mutants between brace lines.
        return open_line + 1, open_line

    # Single-line body, e.g. `int f() { return 0; }`
    return open_line, open_line


def discover_functions_in_file(
    file_path: Path,
    source_root: Path,
) -> list[FunctionRecord]:
    """Discover mutate-able functions in a single source file."""
    rel = file_path.relative_to(source_root)
    text = file_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    records: list[FunctionRecord] = []

    for ctags_line in run_ctags(file_path):
        parsed = parse_ctags_line(ctags_line, file_path)
        if parsed is None:
            continue
        name, line_no, sig = parsed
        if name in BOGUS_CTAGS_NAMES:
            continue
        raw_ret = resolve_return_type(sig, name, lines, line_no)
        if raw_ret is None:
            continue
        if should_skip_function(name, raw_ret):
            continue
        category = classify_return_type(raw_ret)
        if category == "unsupported":
            continue
        span = find_function_body_span(text, line_no)
        if span is None:
            continue
        body_start, body_end = span
        records.append(
            FunctionRecord(
                file=rel,
                name=name,
                line=line_no,
                return_type=normalize_return_type(raw_ret),
                category=category,
                body_start=body_start,
                body_end=body_end,
            )
        )

    # Stable order: file order, then line number.
    records.sort(key=lambda r: (str(r.file), r.line, r.name))
    return records


def discover_all(source_root: Path, exclude_parts: frozenset[str]) -> list[FunctionRecord]:
    """Discover functions in every eligible file under source_root."""
    all_records: list[FunctionRecord] = []
    for path in iter_source_files(source_root, exclude_parts):
        all_records.extend(discover_functions_in_file(path, source_root))
    return all_records


# -----------------------------------------------------------------------------
# Mutation / backup / restore
# -----------------------------------------------------------------------------


def backup_path_for(workdir: Path, rel_file: Path) -> Path:
    """Path where a pristine copy of rel_file is stored during a sweep."""
    return workdir / ".pseudoscope" / "backups" / rel_file


def ensure_backup(workdir: Path, abs_file: Path, rel_file: Path) -> None:
    """Copy the current source file to the backup store if not already present."""
    dest = backup_path_for(workdir, rel_file)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copy2(abs_file, dest)


def restore_from_backup(workdir: Path, abs_file: Path, rel_file: Path) -> None:
    """Restore source from backup (used after each mutant)."""
    src = backup_path_for(workdir, rel_file)
    if not src.exists():
        raise FileNotFoundError(f"Missing backup for restore: {src}")
    # Use copy (not copy2) so mtime updates and Make/ninja rebuild the target.
    shutil.copy(src, abs_file)


def indent_for_line(lines: Sequence[str], body_start: int) -> str:
    """
    Guess indentation for injected statements from the first line inside the body.

    Falls back to two spaces if the body is empty.
    """
    if body_start < len(lines):
        m = re.match(r"^(\s*)", lines[body_start])
        if m and m.group(1):
            return m.group(1)
    return "  "


def apply_mutant(
    abs_file: Path,
    func: FunctionRecord,
    mutant: MutantSpec,
) -> None:
    """Replace the function body with default-return lines only (keep `{` `}`)."""
    text = abs_file.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    plain = [ln.rstrip("\n") for ln in lines]

    indent = indent_for_line(plain, func.body_start)
    new_body_lines = [f"{indent}{stmt}\n" for stmt in mutant.lines]

    line_at_start = lines[func.body_start] if func.body_start < len(lines) else ""
    is_one_line_body = (
        func.body_start == func.body_end
        and "{" in line_at_start
        and "}" in line_at_start
    )

    if is_one_line_body:
        # `int f() { return 0; }` on a single source line.
        open_idx = line_at_start.index("{")
        rebuilt = (
            line_at_start[: open_idx + 1]
            + "\n"
            + "".join(new_body_lines)
            + f"{indent}}}\n"
        )
        new_lines = lines[: func.body_start] + [rebuilt] + lines[func.body_start + 1 :]
    elif func.body_end < func.body_start:
        # Empty body `{` followed immediately by `}` on the next line.
        insert_at = func.body_start
        new_lines = lines[:insert_at] + new_body_lines + lines[insert_at:]
    else:
        new_lines = (
            lines[: func.body_start]
            + new_body_lines
            + lines[func.body_end + 1 :]
        )

    abs_file.write_text("".join(new_lines), encoding="utf-8")
    # Bump mtime so Make-style build tools pick up the mutation after restore/copy.
    abs_file.touch()


# -----------------------------------------------------------------------------
# Build / test / reporting
# -----------------------------------------------------------------------------


def run_shell_command(command: str, cwd: Path, timeout_seconds: int) -> str:
    """
    Run a shell command; return ``pass`` if exit code is 0 else ``fail``.

    Build failures and test failures are both reported as ``fail``.
    """
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return "fail"
    return "pass" if proc.returncode == 0 else "fail"


def shorten_path_for_display(file_path: str, max_len: int = 38) -> str:
    """
    Show a compact path in the results table (last two components).

    Example: ``ujson/python/objToJSON.c`` instead of a long absolute path.
    """
    parts = Path(file_path).parts
    if len(parts) >= 2:
        short = "/".join(parts[-2:])
    else:
        short = parts[-1] if parts else file_path
    if len(short) <= max_len:
        return short
    return "..." + short[-(max_len - 3) :]


def function_outcome(mutant_results: Sequence[str]) -> str:
    """
    Aggregate per-function result from one or two mutant runs.

    Any ``fail`` ⇒ ``fail``; ``pass`` only if every mutant passed.
    """
    if any(r == "fail" for r in mutant_results):
        return "fail"
    return "pass"


def compute_summary(rows: Sequence[SweepRow]) -> SweepSummary:
    """Compute function-level PI (any mutant fail counts as function fail)."""
    if not rows:
        return SweepSummary(0, 0, 0, 0.0, 0, 0, 0)

    mutant_pass = sum(1 for r in rows if r.result == "pass")
    mutant_fail = len(rows) - mutant_pass

    by_function: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        by_function[(row.file, row.function)].append(row.result)

    function_total = len(by_function)
    function_pass = sum(
        1
        for outcomes in by_function.values()
        if function_outcome(outcomes) == "pass"
    )
    function_fail = function_total - function_pass
    pi = 100.0 * function_pass / function_total if function_total else 0.0

    return SweepSummary(
        function_total=function_total,
        function_pass=function_pass,
        function_fail=function_fail,
        pi_percent=pi,
        mutant_total=len(rows),
        mutant_pass=mutant_pass,
        mutant_fail=mutant_fail,
    )


# Column separator for aligned terminal / text tables.
TABLE_SEP = " | "


def format_results_table(rows: Sequence[SweepRow]) -> str:
    """
    Build a fixed-width, pipe-separated table for terminal or file output.

    Columns: File | Function | Mutant | Result (each mutant run is one row).
    """
    if not rows:
        return "(no results)\n"

    display_rows = [
        (
            shorten_path_for_display(r.file),
            r.function,
            r.mutant_id,
            r.result.upper(),
        )
        for r in rows
    ]

    headers = ("File", "Function", "Mutant", "Result")
    widths = [
        max(len(headers[0]), *(len(r[0]) for r in display_rows)),
        max(len(headers[1]), *(len(r[1]) for r in display_rows)),
        max(len(headers[2]), *(len(r[2]) for r in display_rows)),
        max(len(headers[3]), *(len(r[3]) for r in display_rows)),
    ]
    widths[0] = min(widths[0], 42)
    widths[1] = min(widths[1], 32)

    def fmt_row(cells: tuple[str, ...]) -> str:
        file_col = cells[0]
        if len(file_col) > widths[0]:
            file_col = "..." + file_col[-(widths[0] - 3) :]
        parts = (
            f"{file_col:<{widths[0]}}",
            f"{cells[1]:<{widths[1]}}",
            f"{cells[2]:<{widths[2]}}",
            f"{cells[3]:>{widths[3]}}",
        )
        return TABLE_SEP.join(parts)

    sep_len = sum(widths) + len(TABLE_SEP) * (len(widths) - 1)
    sep = "-" * sep_len
    lines = [fmt_row(headers), sep]
    lines.extend(fmt_row(r) for r in display_rows)
    return "\n".join(lines) + "\n"


def format_summary_block(summary: SweepSummary) -> str:
    """Human-readable summary including function-level PI."""
    lines = [
        "",
        "=== Summary ===",
        (
            f"Functions:  {summary.function_total} total  |  "
            f"{summary.function_pass} pass  |  {summary.function_fail} fail"
        ),
        (
            f"PI: {summary.pi_percent:.1f}%  "
            f"— pass only if all default-return mutants passed; "
            f"any fail counts as fail"
        ),
        (
            f"(Mutant runs: {summary.mutant_total} total  |  "
            f"{summary.mutant_pass} pass  |  {summary.mutant_fail} fail)"
        ),
        "",
    ]
    return "\n".join(lines)


def print_sweep_report(
    rows: Sequence[SweepRow],
    *,
    out_csv: Path,
    write_table_file: bool = True,
) -> SweepSummary:
    """
    Print an aligned table and PI summary to stdout; optionally write a .txt table.
    """
    summary = compute_summary(rows)
    table = format_results_table(rows)
    summary_text = format_summary_block(summary)

    print(table, end="")
    print(summary_text)

    if write_table_file:
        table_path = out_csv.with_name(out_csv.stem + "_table.txt")
        table_path.write_text(table + summary_text, encoding="utf-8")
        print(f"Table + summary: {table_path}", file=sys.stderr)

    return summary


def write_csv(path: Path, rows: Sequence[SweepRow]) -> None:
    """Write sweep results as CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["file", "function", "mutant_id", "result"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------


def cmd_discover(args: argparse.Namespace) -> int:
    """List functions under the source tree and optionally write JSON."""
    exclude = frozenset(args.exclude_dir or []) | DEFAULT_EXCLUDE_DIR_PARTS

    if not args.workdir and not args.source_root:
        print("error: specify --workdir and/or --source-root", file=sys.stderr)
        return 1

    workdir = Path(args.workdir).resolve() if args.workdir else Path(".").resolve()
    explicit = Path(args.source_root).resolve() if args.source_root else None
    source_root = infer_source_root(workdir, explicit, exclude)

    if not source_root.is_dir():
        print(f"error: source root not found: {source_root}", file=sys.stderr)
        return 1

    if explicit is None:
        n_files = count_source_files(source_root, exclude)
        print(
            f"Inferred source-root: {source_root} ({n_files} C/C++ file(s))",
            file=sys.stderr,
        )

    records = discover_all(source_root, exclude)
    payload = [
        {
            "file": str(r.file),
            "name": r.name,
            "line": r.line,
            "return_type": r.return_type,
            "category": r.category,
            "body_start": r.body_start,
            "body_end": r.body_end,
        }
        for r in records
    ]

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {len(payload)} functions to {out_path}")
    else:
        print(f"Discovered {len(payload)} functions")
        for item in payload[:20]:
            print(
                f"  {item['file']}:{item['line']} {item['name']} "
                f"({item['category']})"
            )
        if len(payload) > 20:
            print(f"  ... and {len(payload) - 20} more")

    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """Run the mutation sweep loop."""
    workdir = Path(args.workdir).resolve()
    out_csv = Path(args.out).resolve()
    exclude = frozenset(args.exclude_dir or []) | DEFAULT_EXCLUDE_DIR_PARTS
    timeout = args.timeout
    explicit = Path(args.source_root).resolve() if args.source_root else None
    source_root = infer_source_root(workdir, explicit, exclude)

    if not workdir.is_dir():
        print(f"error: workdir not found: {workdir}", file=sys.stderr)
        return 1
    if not source_root.is_dir():
        print(f"error: source root not found: {source_root}", file=sys.stderr)
        return 1

    if explicit is None:
        n_files = count_source_files(source_root, exclude)
        print(
            f"Inferred source-root: {source_root} ({n_files} C/C++ file(s))",
            file=sys.stderr,
        )

    config = SweepConfig(
        workdir=workdir,
        source_root=source_root,
        build_command=args.build_command,
        test_command=args.test_command,
        out_csv=out_csv,
        timeout_seconds=timeout,
        exclude_dir_parts=exclude,
    )

    records = discover_all(source_root, exclude)
    if args.max_functions is not None:
        records = records[: args.max_functions]

    if not records:
        print("No functions to sweep.", file=sys.stderr)
        return 1

    print(
        f"Sweeping {len(records)} function(s); workdir={workdir}\n"
        f"  source-root: {source_root}\n"
        f"  build: {config.build_command}\n"
        f"  test:  {config.test_command}\n",
        file=sys.stderr,
    )

    rows: list[SweepRow] = []
    backups_initialized: set[Path] = set()

    try:
        for index, func in enumerate(records, start=1):
            mutants = MUTANTS_BY_CATEGORY.get(func.category)
            if not mutants:
                continue

            abs_file = source_root / func.file
            if not abs_file.is_file():
                print(f"skip missing file: {func.file}", file=sys.stderr)
                continue

            # Keep one pristine backup per file for the whole sweep.
            if func.file not in backups_initialized:
                ensure_backup(workdir, abs_file, func.file)
                backups_initialized.add(func.file)
            else:
                restore_from_backup(workdir, abs_file, func.file)

            print(
                f"[{index}/{len(records)}] {func.file} :: {func.name} "
                f"({func.category})",
                file=sys.stderr,
            )

            for mutant in mutants:
                restore_from_backup(workdir, abs_file, func.file)
                apply_mutant(abs_file, func, mutant)

                build_result = run_shell_command(
                    config.build_command,
                    config.workdir,
                    config.timeout_seconds,
                )
                if build_result == "fail":
                    test_result = "fail"
                else:
                    test_result = run_shell_command(
                        config.test_command,
                        config.workdir,
                        config.timeout_seconds,
                    )

                row = SweepRow(
                    file=str(func.file),
                    function=func.name,
                    mutant_id=mutant.mutant_id,
                    result=test_result,
                )
                rows.append(row)
                if getattr(args, "live_rows", False):
                    # Optional: one aligned row per mutant (table printed again at end).
                    print(format_results_table([row]), end="")

                restore_from_backup(workdir, abs_file, func.file)

    finally:
        # Always leave the tree as it was before the sweep.
        for rel in backups_initialized:
            abs_file = source_root / rel
            if abs_file.is_file():
                restore_from_backup(workdir, abs_file, rel)

    write_csv(out_csv, rows)
    print_sweep_report(rows, out_csv=out_csv)
    print(f"CSV: {out_csv} ({len(rows)} row(s))", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """CLI definition."""
    parser = argparse.ArgumentParser(
        description="PseudoScope: detect pseudo-tested C/C++ functions via default-return mutation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_discover = sub.add_parser(
        "discover",
        help="List functions under a source tree (requires ctags).",
    )
    p_discover.add_argument(
        "--workdir",
        help="Project root used to infer the scan path when --source-root is omitted.",
    )
    p_discover.add_argument(
        "--source-root",
        help="Directory to scan for C/C++ (default: infer from --workdir).",
    )
    p_discover.add_argument(
        "--out",
        help="Optional JSON output path.",
    )
    p_discover.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        metavar="NAME",
        help="Extra directory name to skip (e.g. test). May be repeated.",
    )
    p_discover.set_defaults(func=cmd_discover)

    p_sweep = sub.add_parser(
        "sweep",
        help="Mutate each function, rebuild, run tests, restore sources.",
    )
    p_sweep.add_argument(
        "--workdir",
        required=True,
        help="Project root where build/test commands run (e.g. ultrajson/).",
    )
    p_sweep.add_argument(
        "--source-root",
        help=(
            "Root of C/C++ sources to mutate. "
            "If omitted, inferred from --workdir (e.g. src/, libCacheSim/libCacheSim/)."
        ),
    )
    p_sweep.add_argument(
        "--build-command",
        default='pip install -e ".[dev]"',
        help='Shell command to rebuild after each mutation (default: pip install -e ".[dev]").',
    )
    p_sweep.add_argument(
        "--test-command",
        default="pytest",
        help="Shell command to run tests (default: pytest).",
    )
    p_sweep.add_argument(
        "--out",
        required=True,
        help="CSV path for results (columns: file, function, mutant_id, result).",
    )
    p_sweep.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Per-command timeout in seconds (default: 600).",
    )
    p_sweep.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        metavar="NAME",
        help="Extra directory name to skip. May be repeated.",
    )
    p_sweep.add_argument(
        "--max-functions",
        type=int,
        default=None,
        help="Limit number of functions (for debugging).",
    )
    p_sweep.add_argument(
        "--live-rows",
        action="store_true",
        help="Print a mini table after each mutant (full table still printed at end).",
    )
    p_sweep.set_defaults(func=cmd_sweep)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
PseudoScope — MVP prototype for detecting pseudo-tested C/C++ functions.

Inspired by PSEUDOSWEEP: delete/replace function bodies and check whether
the test suite fails. Function-level mutation only for this version.

Architecture (modular layers, single file for now):
  1. discovery   — find functions + return types in .c/.cpp sources
  2. mutants     — generate default-return replacements per type
  3. mutation    — backup, patch body, restore (always)
  4. runner      — subprocess build/test with timeout + capture
  5. classifier  — survived / killed / final_classification
  6. reporting   — JSON + table (program | function | test | error)

Later: swap discovery/mutation for Clang LibTooling; add statement-level ops.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class FunctionInfo:
    file: str
    function: str
    line: int
    return_type: str
    body_start: int  # line number of opening '{'
    body_end: int    # line number of closing '}'


@dataclass
class MutantResult:
    replacement: str
    build_result: str
    test_result: str
    classification: str
    build_exit_code: int | None = None
    test_exit_code: int | None = None
    build_duration_sec: float | None = None
    test_duration_sec: float | None = None
    build_stdout: str = ""
    build_stderr: str = ""
    test_stdout: str = ""
    test_stderr: str = ""


@dataclass
class FunctionReport:
    file: str
    function: str
    line: int
    return_type: str
    mutants: list[MutantResult] = field(default_factory=list)
    final_classification: str = ""


# ---------------------------------------------------------------------------
# Discovery — simple regex + brace matching (replaceable with Tree-sitter/Clang)
# ---------------------------------------------------------------------------

SKIP_NAME_PREFIXES = ("~",)
SKIP_NAME_KEYWORDS = {"operator"}
SKIP_RET_PATTERNS = [
    re.compile(r"template\s*<"),
    re.compile(r"\boperator\b"),
]
SOURCE_SUFFIXES = {".c", ".cpp", ".cc", ".cxx"}


def _normalize_return_type(raw: str) -> str:
    raw = re.sub(r"\s+", " ", raw.strip())
    if raw.startswith("const "):
        raw = raw[6:].strip()
    return raw


def _is_skipped_function(name: str, ret: str, params: str, context: str) -> bool:
    if any(name.startswith(p) for p in SKIP_NAME_PREFIXES):
        return True
    if name in SKIP_NAME_KEYWORDS or name.startswith("operator"):
        return True
    if "<" in ret or ">" in ret:
        return True
    if "[" in params and "]" in params and "=" in params:
        return True  # likely lambda assignment
    if "template" in context.lower():
        return True
    for pat in SKIP_RET_PATTERNS:
        if pat.search(ret):
            return True
    return False


def _find_brace_block(lines: list[str], open_line: int) -> int | None:
    """Return 1-based line number of matching closing brace."""
    depth = 0
    for i in range(open_line - 1, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
    return None


# Match: return_type name(params) [const] {
# Handles multi-line params by scanning until ')' then '{'.
FUNC_HEAD_RE = re.compile(
    r"^(\s*)"
    r"([\w:\<\>\s\*&]+?)\s+"
    r"(\w+)\s*"
    r"\(([^;]*)\)\s*"
    r"(?:const\s*)?"
    r"\{"
)


def discover_functions(
    source_dir: Path,
    files_filter: list[str] | None = None,
) -> list[FunctionInfo]:
    functions: list[FunctionInfo] = []
    for path in sorted(source_dir.rglob("*")):
        if path.suffix not in SOURCE_SUFFIXES:
            continue
        if files_filter and path.name not in files_filter:
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        rel = str(path.relative_to(source_dir.parent if source_dir.name == "src" else source_dir))
        # Keep path relative to the --source argument's parent when possible
        rel = str(path.relative_to(source_dir))

        i = 0
        while i < len(lines):
            line = lines[i]
            # Accumulate multi-line signatures until we see ') {'
            sig_lines = [line]
            j = i
            while j < len(lines) and "{" not in "".join(sig_lines):
                if j > i:
                    sig_lines.append(lines[j])
                if ")" in lines[j] and "{" in lines[j]:
                    break
                if ")" in lines[j] and j + 1 < len(lines) and "{" in lines[j + 1]:
                    sig_lines.append(lines[j + 1])
                    j += 1
                    break
                j += 1
                if j > i and j < len(lines) and "{" not in lines[j]:
                    continue
                if j > i:
                    break

            combined = "".join(sig_lines).strip()
            m = FUNC_HEAD_RE.match(combined.replace("\n", " "))
            if not m:
                # Try joining current line only
                m = FUNC_HEAD_RE.match(line.strip())
            if m:
                ret = _normalize_return_type(m.group(2))
                name = m.group(3)
                params = m.group(4)
                context = "\n".join(lines[max(0, i - 3) : i + 1])
                if not _is_skipped_function(name, ret, params, context):
                    open_line = i + 1
                    for k in range(i, min(j, len(lines) - 1) + 1):
                        if "{" in lines[k]:
                            open_line = k + 1
                            break
                    close_line = _find_brace_block(lines, open_line)
                    if close_line:
                        functions.append(
                            FunctionInfo(
                                file=rel,
                                function=name,
                                line=i + 1,
                                return_type=ret,
                                body_start=open_line,
                                body_end=close_line,
                            )
                        )
                i = j + 1
                continue
            i += 1

    return functions


def classify_return_type(ret: str) -> str | None:
    """Map return type to a supported mutant family, or None."""
    r = ret.strip()
    if r == "void":
        return "void"
    if r == "bool":
        return "bool"
    if r in {"int", "long", "short", "size_t", "unsigned int", "unsigned long",
             "unsigned short", "long long", "unsigned long long"}:
        return "int"
    if r in {"float", "double"}:
        return "float"
    if r == "std::string":
        return "std_string"
    if "*" in r or r.endswith("*") or r.startswith("char *") or r.startswith("char*"):
        return "pointer"
    return None


def generate_mutants(return_type: str) -> list[str] | None:
    family = classify_return_type(return_type)
    if family is None:
        return None
    table: dict[str, list[str]] = {
        "void": ["return;"],
        "bool": ["return false;", "return true;"],
        "int": ["return 0;", "return 1;"],
        "float": ["return 0.0;", "return 1.0;"],
        "pointer": ["return nullptr;"],
        "std_string": ['return "";', 'return "A";'],
    }
    return table[family]


# ---------------------------------------------------------------------------
# Mutation — backup, replace body interior, restore
# ---------------------------------------------------------------------------

def _backup_path(project_root: Path, source_file: Path) -> Path:
    rel = source_file.relative_to(project_root)
    dest = project_root / ".pseudoscope" / "backups" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def backup_file(project_root: Path, source_file: Path) -> None:
    dest = _backup_path(project_root, source_file)
    shutil.copy2(source_file, dest)


def restore_file(project_root: Path, source_file: Path) -> None:
    dest = _backup_path(project_root, source_file)
    if dest.exists():
        shutil.copy2(dest, source_file)


def apply_mutation(source_file: Path, func: FunctionInfo, replacement: str) -> None:
    lines = source_file.read_text(encoding="utf-8").splitlines(keepends=True)
    start = func.body_start - 1
    end = func.body_end - 1
    indent = re.match(r"^(\s*)", lines[start]).group(1) + "    "
    new_body = [lines[start]]
    new_body.append(f"{indent}{replacement}\n")
    new_body.append(lines[end])
    new_lines = lines[:start] + new_body + lines[end + 1 :]
    source_file.write_text("".join(new_lines), encoding="utf-8")
    # Ensure Make/ninja rebuilds even when incremental rules miss the change.
    source_file.touch()


# ---------------------------------------------------------------------------
# Command runner
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    timed_out: bool = False


def run_command(
    command: str,
    cwd: Path,
    timeout: int,
) -> CommandResult:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.monotonic() - start
        return CommandResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_sec=duration,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return CommandResult(
            exit_code=-1,
            stdout=stdout,
            stderr=stderr + f"\n[timeout after {timeout}s]",
            duration_sec=duration,
            timed_out=True,
        )


def result_label(cmd: CommandResult) -> str:
    if cmd.timed_out:
        return "timeout"
    return "pass" if cmd.exit_code == 0 else "fail"


def mutant_classification(build: CommandResult, test: CommandResult | None) -> tuple[str, str, str]:
    build_res = result_label(build)
    if build_res != "pass":
        return build_res, "skip", "build_failed"
    if test is None:
        return build_res, "skip", "build_failed"
    test_res = result_label(test)
    if test_res == "pass":
        return build_res, test_res, "survived"
    return build_res, test_res, "killed"


def final_classification(
    mutants: list[MutantResult],
    supported: bool,
) -> str:
    if not supported:
        return "unsupported"
    if not mutants:
        return "unsupported"
    if any(m.classification == "build_failed" for m in mutants):
        if all(m.classification == "build_failed" for m in mutants):
            return "build_failed"
    survived = [m for m in mutants if m.classification == "survived"]
    killed = [m for m in mutants if m.classification == "killed"]
    if survived and not killed:
        return "pseudo_tested"
    if killed and not survived:
        return "killed"
    if survived and killed:
        return "partially_detected"
    return "build_failed"


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

def sweep_functions(
    functions_path: Path,
    project_root: Path,
    source_root: Path,
    build_command: str,
    test_command: str,
    timeout: int,
) -> list[FunctionReport]:
    raw = json.loads(functions_path.read_text(encoding="utf-8"))
    reports: list[FunctionReport] = []

    for entry in raw:
        func = FunctionInfo(**entry)
        source_file = source_root / func.file
        if not source_file.exists():
            source_file = project_root / func.file

        mutants_spec = generate_mutants(func.return_type)
        report = FunctionReport(
            file=func.file,
            function=func.function,
            line=func.line,
            return_type=func.return_type,
        )

        if mutants_spec is None:
            report.final_classification = "unsupported"
            reports.append(report)
            continue

        backup_file(project_root, source_file)

        try:
            for replacement in mutants_spec:
                restore_file(project_root, source_file)
                apply_mutation(source_file, func, replacement)

                build = run_command(build_command, project_root, timeout)
                test: CommandResult | None = None
                if result_label(build) == "pass":
                    test = run_command(test_command, project_root, timeout)

                b_res, t_res, cls = mutant_classification(build, test)
                report.mutants.append(
                    MutantResult(
                        replacement=replacement,
                        build_result=b_res,
                        test_result=t_res,
                        classification=cls,
                        build_exit_code=build.exit_code,
                        test_exit_code=test.exit_code if test else None,
                        build_duration_sec=round(build.duration_sec, 3),
                        test_duration_sec=round(test.duration_sec, 3) if test else None,
                        build_stdout=build.stdout[-4000:],
                        build_stderr=build.stderr[-4000:],
                        test_stdout=test.stdout[-4000:] if test else "",
                        test_stderr=test.stderr[-4000:] if test else "",
                    )
                )
        finally:
            restore_file(project_root, source_file)

        report.final_classification = final_classification(report.mutants, True)
        reports.append(report)

    return reports


# ---------------------------------------------------------------------------
# Table report — program name | function | test name | error message
# ---------------------------------------------------------------------------

FAIL_BLOCK_RE = re.compile(r"FAIL:\s*(.+?)\s+expected\b", re.DOTALL)
ASSERT_LINE_RE = re.compile(
    r"Assertion failed:\s*\((.+?)\),\s*function\s+\w+,\s*file\s+(.+?),\s*line\s+(\d+)\.",
    re.DOTALL,
)


def parse_test_failures(stderr: str) -> list[tuple[str, str]]:
    """Extract (test_name, error_message) pairs from test stderr."""
    if not stderr.strip():
        return []

    rows: list[tuple[str, str]] = []

    for m in FAIL_BLOCK_RE.finditer(stderr):
        test_name = m.group(1).strip()
        start = m.start()
        next_fail = stderr.find("\nFAIL:", start + 1)
        end = next_fail if next_fail != -1 else len(stderr)
        error = stderr[start:end].strip()
        rows.append((test_name, error))

    for m in ASSERT_LINE_RE.finditer(stderr):
        expr, file_name, line_no = m.groups()
        test_name = f"{file_name}:{line_no} {expr}"
        rows.append((test_name, m.group(0).strip()))

    if not rows and stderr.strip():
        rows.append(("(unknown)", stderr.strip()))
    return rows


def parse_build_failure(stderr: str) -> tuple[str, str] | None:
    """Pick a representative compiler error line from build stderr."""
    if not stderr.strip():
        return None
    for line in stderr.splitlines():
        line = line.strip()
        if "error:" in line:
            return ("(build)", line)
    last = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    if last:
        return ("(build)", last[-1])
    return None


@dataclass
class TableRow:
    program_name: str
    function_name: str
    test_name: str
    error_message: str
    mutant: str = ""


def results_to_table_rows(
    results: list[dict],
    program_name: str,
    include_passed: bool = False,
) -> list[TableRow]:
    rows: list[TableRow] = []
    for entry in results:
        func_name = entry["function"]
        for mutant in entry.get("mutants", []):
            replacement = mutant.get("replacement", "")
            if mutant.get("build_result") == "fail":
                parsed = parse_build_failure(mutant.get("build_stderr", ""))
                if parsed:
                    test_name, err = parsed
                    rows.append(
                        TableRow(program_name, func_name, test_name, err, replacement)
                    )
                continue

            if mutant.get("test_result") == "fail":
                failures = parse_test_failures(mutant.get("test_stderr", ""))
                if failures:
                    for test_name, err in failures:
                        rows.append(
                            TableRow(
                                program_name, func_name, test_name, err, replacement
                            )
                        )
                else:
                    rows.append(
                        TableRow(
                            program_name,
                            func_name,
                            "(test failed)",
                            mutant.get("test_stderr", "").strip() or "(no message)",
                            replacement,
                        )
                    )
            elif include_passed and mutant.get("test_result") == "pass":
                rows.append(
                    TableRow(
                        program_name,
                        func_name,
                        "(passed)",
                        mutant.get("test_stdout", "").strip() or "(tests passed)",
                        replacement,
                    )
                )
    return rows


def _escape_cell(value: str) -> str:
    """Keep table rows on one line when messages contain newlines."""
    return value.replace("\r", "").replace("\n", "\\n")


def format_table(rows: list[TableRow], delimiter: str = " | ") -> str:
    """Pipe-style table for terminal viewing."""
    header = ["program name", "Function name", "Test name", "error message"]
    lines = [delimiter.join(header)]
    for r in rows:
        lines.append(
            delimiter.join(
                [
                    _escape_cell(r.program_name),
                    _escape_cell(r.function_name),
                    _escape_cell(r.test_name),
                    _escape_cell(r.error_message),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def format_csv(rows: list[TableRow]) -> str:
    """RFC-style CSV (quoted fields) for Excel / Google Sheets."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["program name", "Function name", "Test name", "error message"])
    for r in rows:
        writer.writerow(
            [r.program_name, r.function_name, r.test_name, r.error_message]
        )
    return buf.getvalue()


def infer_program_name(results_path: Path) -> str:
    """Use parent-of-.pseudoscope directory name, e.g. Test/.pseudoscope/... -> Test."""
    parts = results_path.resolve().parts
    if ".pseudoscope" in parts:
        idx = parts.index(".pseudoscope")
        if idx > 0:
            return parts[idx - 1]
    return results_path.parent.name


def cmd_report(args: argparse.Namespace) -> None:
    results_path = Path(args.results).resolve()
    program_name = args.program or infer_program_name(results_path)
    raw = json.loads(results_path.read_text(encoding="utf-8"))
    rows = results_to_table_rows(raw, program_name, include_passed=args.include_passed)
    if args.format == "csv":
        text = format_csv(rows)
    else:
        text = format_table(rows, delimiter=args.delimiter)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Report ({len(rows)} row(s), {args.format}) -> {out}")
    else:
        print(text, end="")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_discover(args: argparse.Namespace) -> None:
    source = Path(args.source).resolve()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    files_filter = args.files.split(",") if args.files else None
    functions = discover_functions(source, files_filter=files_filter)
    payload = [asdict(f) for f in functions]
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Discovered {len(functions)} function(s) -> {out}")


def cmd_sweep(args: argparse.Namespace) -> None:
    functions_path = Path(args.functions).resolve()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    project_root = Path(args.workdir).resolve()
    source_root = Path(args.source_root).resolve() if args.source_root else project_root

    reports = sweep_functions(
        functions_path=functions_path,
        project_root=project_root,
        source_root=source_root,
        build_command=args.build_command,
        test_command=args.test_command,
        timeout=args.timeout,
    )

    payload = []
    for r in reports:
        item = {
            "file": r.file,
            "function": r.function,
            "line": r.line,
            "return_type": r.return_type,
            "mutants": [asdict(m) for m in r.mutants],
            "final_classification": r.final_classification,
        }
        payload.append(item)

    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Sweep complete -> {out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PseudoScope: detect pseudo-tested C/C++ functions (MVP)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_disc = sub.add_parser("discover", help="Find functions in source tree")
    p_disc.add_argument("--source", required=True, help="Source directory (.c/.cpp)")
    p_disc.add_argument("--out", required=True, help="Output functions.json")
    p_disc.add_argument("--files", default=None,
                        help="Comma-separated filenames to include (e.g. hello.cpp)")
    p_disc.set_defaults(func=cmd_discover)

    p_swp = sub.add_parser("sweep", help="Run mutation testing sweep")
    p_swp.add_argument("--functions", required=True, help="functions.json from discover")
    p_swp.add_argument("--build-command", required=True, help="Shell command to build")
    p_swp.add_argument("--test-command", required=True, help="Shell command to run tests")
    p_swp.add_argument("--out", required=True, help="Output results.json")
    p_swp.add_argument("--workdir", default=".", help="Project root for build/test cwd")
    p_swp.add_argument("--source-root", default=None,
                       help="Root for source files (default: workdir)")
    p_swp.add_argument("--timeout", type=int, default=120,
                       help="Timeout in seconds for build/test")
    p_swp.set_defaults(func=cmd_sweep)

    p_rep = sub.add_parser("report", help="Format results.json as a table")
    p_rep.add_argument("--results", required=True, help="results.json from sweep")
    p_rep.add_argument("--program", default=None,
                       help="Program name column (default: inferred from path)")
    p_rep.add_argument("--out", default=None, help="Write table to file (default: stdout)")
    p_rep.add_argument("--format", choices=["pipe", "csv"], default="pipe",
                       help="Output format: pipe (terminal) or csv (Excel)")
    p_rep.add_argument("--delimiter", default=" | ",
                       help="Column delimiter for pipe format (default: ' | ')")
    p_rep.add_argument("--include-passed", action="store_true",
                       help="Include rows where tests passed (survived mutants)")
    p_rep.set_defaults(func=cmd_report)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

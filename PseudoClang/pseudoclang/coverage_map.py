"""
Load and query a pstrace coverage map (``pstrace-coverage/1``).

The map answers "which tests exercise function F in file P?" so the sweep can
run only the tests that touch a mutated function instead of the whole suite.

This module is pure: it parses, validates, looks up, and decides an execution
plan. It performs no I/O beyond reading the map file and prints nothing. All
warnings / orchestration live in the caller (``analysis``/``sweep``/``cli``).
"""

from __future__ import annotations

import enum
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Frozen schema identifier this loader understands. Refuse anything else.
SUPPORTED_SCHEMA = "pstrace-coverage/1"

#: Token a function's nodeid list may contain meaning "called at import time".
#: It is not a runnable pytest selector.
STARTUP_TOKEN = "(startup)"

# Provenance labels: HOW a function's mutants were (or would be) judged.
JUDGMENT_SELECTED = "selected"
JUDGMENT_FULL_ABSENT = "full_fallback_absent"
JUDGMENT_FULL_STARTUP_ONLY = "full_fallback_startup_only"
JUDGMENT_SKIPPED_UNCOVERED = "skipped_assumed_uncovered"
JUDGMENT_FULL_NO_MAP = "full_no_map"
JUDGMENT_FULL_NO_TEMPLATE = "full_no_template"
#: A SELECTED plan whose subset failed to cleanly pass against the original source
#: (e.g. the map's nodeids no longer collect any test): degraded to a full run
#: because the subset is not a trustworthy judge. See analysis.guard_selected_plan.
JUDGMENT_FULL_SELECTED_UNTRUSTWORTHY = "full_fallback_selected_baseline_failed"


class CoverageMapError(Exception):
    """Raised when the coverage map is missing, malformed, or incompatible."""


class SelectionKind(enum.Enum):
    """Result of looking up one ``(rel_path, func_name)`` in the map."""

    SELECTED = "selected"
    STARTUP_ONLY = "startup_only"
    ABSENT = "absent"


@dataclass(frozen=True)
class Selection:
    """A lookup result: a state plus the runnable nodeids when SELECTED."""

    kind: SelectionKind
    nodeids: tuple[str, ...] = ()


class PlanKind(enum.Enum):
    """What the sweep should do for a function's mutants."""

    RUN_SELECTED = "run_selected"
    RUN_FULL = "run_full"
    SKIP_AS_SURVIVED = "skip_as_survived"


@dataclass(frozen=True)
class ExecutionPlan:
    """A decision plus the provenance label to record in the results."""

    kind: PlanKind
    judgment: str
    nodeids: tuple[str, ...] = ()


def decide_execution(selection: Selection, *, assume_complete: bool) -> ExecutionPlan:
    """
    Map a :class:`Selection` to an :class:`ExecutionPlan` (the core policy).

    - ``SELECTED``      -> run only those nodeids (fast path).
    - ``STARTUP_ONLY``  -> run the full suite. The function executes at import;
      deleting its body can break import itself (failing every test), so it
      cannot be judged by a subset and must never be skipped.
    - ``ABSENT`` + ``assume_complete``     -> skip, record as survived (fast,
      less safe: only for serial, subprocess/thread-free suites).
    - ``ABSENT`` (default, safe)           -> run the full suite. Absent may be a
      true "uncovered" or a pstrace blind spot (subprocess/thread calls), so the
      full suite avoids a false pseudo-tested verdict.

    Pure and side-effect free for unit testing.
    """
    if selection.kind is SelectionKind.SELECTED:
        return ExecutionPlan(
            PlanKind.RUN_SELECTED,
            judgment=JUDGMENT_SELECTED,
            nodeids=selection.nodeids,
        )
    if selection.kind is SelectionKind.STARTUP_ONLY:
        return ExecutionPlan(PlanKind.RUN_FULL, judgment=JUDGMENT_FULL_STARTUP_ONLY)
    # ABSENT
    if assume_complete:
        return ExecutionPlan(
            PlanKind.SKIP_AS_SURVIVED, judgment=JUDGMENT_SKIPPED_UNCOVERED
        )
    return ExecutionPlan(PlanKind.RUN_FULL, judgment=JUDGMENT_FULL_ABSENT)


class CoverageMap:
    """A parsed, validated coverage map with lookup and metadata accessors."""

    def __init__(
        self,
        *,
        coverage: dict[str, dict[str, list[str]]],
        tests: list[str],
        meta: dict[str, Any],
    ) -> None:
        self._coverage = coverage
        self._tests = tests
        self._meta = meta
        self._universe = set(tests)

    # -- metadata -----------------------------------------------------------
    @property
    def meta(self) -> dict[str, Any]:
        return self._meta

    @property
    def project_root(self) -> str:
        return str(self._meta.get("project_root", ""))

    @property
    def image(self) -> Any:
        return self._meta.get("image")

    @property
    def created_at(self) -> Any:
        return self._meta.get("created_at")

    # -- queries ------------------------------------------------------------
    def universe(self) -> set[str]:
        """The full set of tests that passed when the map was captured."""
        return set(self._universe)

    def has_file(self, rel_path: str | Path) -> bool:
        """True if the map carries any coverage data for ``rel_path``."""
        return self._normalize_rel(rel_path) in self._coverage

    def _normalize_rel(self, rel_path: str | Path) -> str:
        """Normalize a file path to a project-root-relative POSIX key."""
        path = Path(rel_path)
        if path.is_absolute():
            root = self.project_root
            try:
                path = path.relative_to(root) if root else path
            except ValueError:
                if root:
                    path = Path(os.path.relpath(path, root))
        return path.as_posix()

    def lookup(self, rel_path: str | Path, func_name: str) -> Selection:
        """
        Resolve ``(rel_path, func_name)`` to a :class:`Selection`.

        Drops ``"(startup)"`` from the runnable list and de-duplicates / sorts
        the remaining nodeids.
        """
        file_map = self._coverage.get(self._normalize_rel(rel_path))
        if not file_map or func_name not in file_map:
            return Selection(SelectionKind.ABSENT)

        raw = file_map[func_name]
        runnable = sorted({n for n in raw if n != STARTUP_TOKEN})
        if runnable:
            return Selection(SelectionKind.SELECTED, tuple(runnable))
        if STARTUP_TOKEN in raw:
            return Selection(SelectionKind.STARTUP_ONLY)
        # Empty/unknown-only list: treat as no coverage.
        return Selection(SelectionKind.ABSENT)


def load_coverage_map(path: str | Path | None) -> CoverageMap | None:
    """
    Load and validate a coverage map from ``path``.

    Returns ``None`` when ``path`` is ``None`` (selection disabled). Raises
    :class:`CoverageMapError` on a missing/unreadable file, invalid JSON, a
    schema mismatch, or a structurally wrong document. The map is read-only.
    """
    if path is None:
        return None

    map_path = Path(path)
    try:
        text = map_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CoverageMapError(f"Cannot read coverage map {map_path}: {exc}") from exc

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CoverageMapError(
            f"Coverage map {map_path} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(document, dict):
        raise CoverageMapError(
            f"Coverage map {map_path} must be a JSON object at the top level."
        )

    meta = document.get("meta")
    if not isinstance(meta, dict):
        raise CoverageMapError(f"Coverage map {map_path} is missing a 'meta' object.")

    schema = meta.get("schema")
    if schema != SUPPORTED_SCHEMA:
        raise CoverageMapError(
            f"Coverage map {map_path} has schema {schema!r}; "
            f"this build only understands {SUPPORTED_SCHEMA!r}. "
            "Refusing to misinterpret a different schema."
        )

    project_root = meta.get("project_root")
    if not isinstance(project_root, str) or not project_root:
        raise CoverageMapError(
            f"Coverage map {map_path} 'meta.project_root' must be a non-empty string."
        )

    coverage = _validate_coverage(document.get("coverage"), map_path)
    tests = _validate_tests(document.get("tests"), map_path)

    return CoverageMap(coverage=coverage, tests=tests, meta=meta)


def verify_project_root(coverage_map: CoverageMap, project_root: str | Path) -> None:
    """
    Ensure the map was captured against ``project_root`` (compare realpaths).

    Raises :class:`CoverageMapError` if they differ: a map from a different tree
    has relative paths and nodeids that may not apply here.
    """
    map_root = os.path.realpath(coverage_map.project_root)
    run_root = os.path.realpath(str(project_root))
    if map_root != run_root:
        raise CoverageMapError(
            "Coverage map was captured against a different project root.\n"
            f"  map  meta.project_root: {map_root}\n"
            f"  run  --project-root-source-dir: {run_root}\n"
            "Its relative paths and nodeids may not apply; refusing to proceed."
        )


def _validate_coverage(coverage: Any, map_path: Path) -> dict[str, dict[str, list[str]]]:
    if not isinstance(coverage, dict):
        raise CoverageMapError(
            f"Coverage map {map_path} 'coverage' must be an object."
        )
    for rel_path, file_map in coverage.items():
        if not isinstance(rel_path, str) or not isinstance(file_map, dict):
            raise CoverageMapError(
                f"Coverage map {map_path} 'coverage[{rel_path!r}]' must map "
                "file paths to function objects."
            )
        for func_name, nodeids in file_map.items():
            if not isinstance(func_name, str) or not isinstance(nodeids, list):
                raise CoverageMapError(
                    f"Coverage map {map_path} 'coverage[{rel_path!r}]"
                    f"[{func_name!r}]' must map function names to nodeid lists."
                )
            if not all(isinstance(n, str) for n in nodeids):
                raise CoverageMapError(
                    f"Coverage map {map_path} 'coverage[{rel_path!r}]"
                    f"[{func_name!r}]' must contain only string nodeids."
                )
    return coverage


def _validate_tests(tests: Any, map_path: Path) -> list[str]:
    if not isinstance(tests, list) or not all(isinstance(t, str) for t in tests):
        raise CoverageMapError(
            f"Coverage map {map_path} 'tests' must be a list of strings."
        )
    return tests

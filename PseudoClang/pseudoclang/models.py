"""Data models for PseudoScope (no I/O side effects)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Raised when user input fails validation."""


@dataclass(frozen=True)
class PseudoScopeConfig:
    """
    Validated, normalized inputs for one PseudoScope analysis run.

    Step 1 only stores configuration. Later steps will use these fields to:
    read the target file, locate/delete the function body, run tests, restore
    the file, and write JSON results — without changing this dataclass shape.
    """

    project_root: Path
    relative_file_path: Path | None
    target_file: Path | None
    function_name: str | None
    test_command: str
    output_path: Path
    timeout_seconds: int
    mode: str | None
    lang: str | None
    # Optional pstrace-driven test selection. Defaults keep behavior identical
    # to a run with no coverage map.
    coverage_map_path: Path | None = None
    assume_coverage_complete: bool = False
    test_runner_template: str | None = None

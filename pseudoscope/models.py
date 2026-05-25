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
    relative_file_path: Path
    target_file: Path
    function_name: str
    test_command: str
    output_path: Path
    timeout_seconds: int

"""
Parse, normalize, and validate CLI input (Step 1).

This module performs read-only checks (e.g. ``Path.exists()``). It does not:
- read or modify source file contents
- run the test command
- create the output JSON file
"""

from __future__ import annotations

from pathlib import Path

from pseudoscope.discover import SWEEP_SOURCE_SUFFIXES
from pseudoscope.models import ConfigError, PseudoScopeConfig


def _require_non_empty(value: str, field_name: str) -> str:
    text = value.strip()
    if not text:
        raise ConfigError(f"{field_name} must not be empty.")
    return text


def normalize_relative_file_path(file: str) -> Path:
    """
    Normalize ``--file`` as a path relative to the project root.

    Strips surrounding whitespace, rejects absolute paths and ``..`` segments.
    """
    raw = file.strip()
    if not raw:
        raise ConfigError("File path must not be empty.")

    relative = Path(raw)
    if relative.is_absolute():
        raise ConfigError(
            "--file must be a path relative to --project-root, not an absolute path."
        )

    parts = [part for part in relative.parts if part not in (".", "")]
    if not parts:
        raise ConfigError("File path must not be empty.")
    if ".." in parts:
        raise ConfigError("--file must not contain '..' components.")

    return Path(*parts)


def normalize_output_path(output: str, project_root: Path) -> Path:
    """Resolve ``--output`` relative to the project root when not absolute."""
    raw = output.strip()
    if not raw:
        raise ConfigError("Output path must not be empty.")

    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (project_root / path).resolve()


def resolve_target_file(project_root: Path, relative_file_path: Path) -> Path:
    """Join paths and ensure the target stays inside the project root."""
    root = project_root.resolve()
    target = (root / relative_file_path).resolve()

    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ConfigError(
            f"Target file resolves outside the project root: {target}"
        ) from exc

    return target


def validate_project_root(project_root: str) -> Path:
    """Ensure the project root exists and is a directory."""
    root = Path(project_root).expanduser().resolve()
    if not root.exists():
        raise ConfigError(f"Project root does not exist: {root}")
    if not root.is_dir():
        raise ConfigError(f"Project root is not a directory: {root}")
    return root


def validate_target_file_exists(target_file: Path) -> None:
    """Read-only metadata check; does not open or modify the file."""
    if not target_file.exists():
        raise ConfigError(f"Target file does not exist: {target_file}")
    if not target_file.is_file():
        raise ConfigError(f"Target path is not a file: {target_file}")


def validate_timeout(timeout: int) -> int:
    if timeout <= 0:
        raise ConfigError(f"Timeout must be a positive integer (got {timeout}).")
    return timeout


def validate_sweep_file_extension(relative_file_path: Path) -> None:
    """Require a C/C++ source extension when running a file sweep."""
    suffix = relative_file_path.suffix.lower()
    if suffix not in SWEEP_SOURCE_SUFFIXES:
        supported = ", ".join(sorted(SWEEP_SOURCE_SUFFIXES))
        raise ConfigError(
            f"File sweep requires a source file with extension {supported} "
            f"(got {relative_file_path.name!r})."
        )


def build_config(
    *,
    project_root: str,
    file: str,
    function: str | None,
    test_command: str,
    output: str,
    timeout: int,
) -> PseudoScopeConfig:
    """
    Normalize and validate all CLI fields into a :class:`PseudoScopeConfig`.

    Raises :class:`ConfigError` with a human-readable message on failure.
    """
    root = validate_project_root(project_root)
    relative_file_path = normalize_relative_file_path(file)
    target_file = resolve_target_file(root, relative_file_path)
    validate_target_file_exists(target_file)

    if function is None or not function.strip():
        function_name = None
        validate_sweep_file_extension(relative_file_path)
    else:
        function_name = _require_non_empty(function, "Function name")
    command = _require_non_empty(test_command, "Test command")
    timeout_seconds = validate_timeout(timeout)
    output_path = normalize_output_path(output, root)

    return PseudoScopeConfig(
        project_root=root,
        relative_file_path=relative_file_path,
        target_file=target_file,
        function_name=function_name,
        test_command=command,
        output_path=output_path,
        timeout_seconds=timeout_seconds,
    )

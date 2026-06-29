"""
Parse, normalize, and validate CLI input (Step 1).

This module performs read-only checks (e.g. ``Path.exists()``). It does not:
- read or modify source file contents
- run the test command
- create the output JSON file
"""

from __future__ import annotations

from pathlib import Path

from pseudoclang.discover import SWEEP_SOURCE_SUFFIXES
from pseudoclang.models import ConfigError, PseudoScopeConfig


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
            "--file must be a path relative to --project-root-source-dir, "
            "not an absolute path."
        )

    parts = [part for part in relative.parts if part not in (".", "")]
    if not parts:
        raise ConfigError("File path must not be empty.")
    if ".." in parts:
        raise ConfigError("--file must not contain '..' components.")

    return Path(*parts)


DEFAULT_OUTPUT_FILE = "pseudoclang-results.json"


def resolve_output_path(
    *,
    output_dir: str | None,
    output_file: str | None,
    project_root: Path,
) -> Path:
    """Join ``--output-dir`` and ``--output-file`` into a single output path."""
    file_raw = output_file.strip() if output_file else ""
    file_name = file_raw or DEFAULT_OUTPUT_FILE
    if not file_name or file_name in (".", ".."):
        raise ConfigError("--output-file must be a valid file name.")

    if output_dir is None or not output_dir.strip():
        parent = project_root
    else:
        dir_path = Path(output_dir.strip()).expanduser()
        parent = dir_path.resolve() if dir_path.is_absolute() else (
            project_root / dir_path
        ).resolve()

    return (parent / file_name).resolve()


def validate_mode(mode: str | None) -> str | None:
    """Stub: stored in results JSON; not used by the pipeline yet."""
    if mode is None:
        return None
    text = mode.strip()
    if not text:
        raise ConfigError("--mode must not be empty when provided.")
    return text


def validate_lang(lang: str | None) -> str | None:
    """Stub: stored in results JSON; not used by the pipeline yet."""
    if lang is None:
        return None
    text = lang.strip()
    if not text:
        raise ConfigError("--lang must not be empty when provided.")
    return text


def require_target_file(config: PseudoScopeConfig) -> None:
    """Ensure a target source file is set before running analysis."""
    if config.target_file is None or config.relative_file_path is None:
        raise ConfigError(
            "--file is required for analysis in this release. "
            "Provide a path relative to --project-root-source-dir."
        )


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


def resolve_coverage_map_path(
    coverage_map: str | None,
    *,
    project_root: Path,
) -> Path | None:
    """Resolve ``--coverage-map`` to an absolute path (relative to the root)."""
    if coverage_map is None or not coverage_map.strip():
        return None
    raw = Path(coverage_map.strip()).expanduser()
    resolved = raw if raw.is_absolute() else (project_root / raw)
    return resolved.resolve()


def validate_selection_options(
    *,
    coverage_map_path: Path | None,
    assume_coverage_complete: bool,
    test_runner_template: str | None,
) -> str | None:
    """
    Validate the test-selection flags and return the normalized template.

    - ``--assume-coverage-complete`` requires ``--coverage-map`` (nothing to be
      complete about otherwise).
    - ``--test-runner-template``, when given, must contain the ``{selection}``
      placeholder so selected nodeids actually reach the runner.
    """
    if assume_coverage_complete and coverage_map_path is None:
        raise ConfigError(
            "--assume-coverage-complete requires --coverage-map."
        )

    if test_runner_template is None:
        return None
    template = test_runner_template.strip()
    if not template:
        raise ConfigError("--test-runner-template must not be empty when provided.")
    if "{selection}" not in template:
        raise ConfigError(
            "--test-runner-template must contain the '{selection}' placeholder "
            "where selected test nodeids are inserted."
        )
    return template


def build_config(
    *,
    project_root_source_dir: str,
    file: str | None,
    function: str | None,
    test_command: str,
    output_dir: str | None,
    output_file: str | None,
    timeout: int,
    mode: str | None,
    lang: str | None,
    coverage_map: str | None = None,
    assume_coverage_complete: bool = False,
    test_runner_template: str | None = None,
    coverage_map_cmd: str | None = None,
    refresh_coverage_map: bool = False,
) -> PseudoScopeConfig:
    """
    Normalize and validate all CLI fields into a :class:`PseudoScopeConfig`.

    Raises :class:`ConfigError` with a human-readable message on failure.
    """
    root = validate_project_root(project_root_source_dir)
    command = _require_non_empty(test_command, "Test command")
    timeout_seconds = validate_timeout(timeout)
    output_path = resolve_output_path(
        output_dir=output_dir,
        output_file=output_file,
        project_root=root,
    )
    mode_value = validate_mode(mode)
    lang_value = validate_lang(lang)

    coverage_map_path = resolve_coverage_map_path(coverage_map, project_root=root)
    template_value = validate_selection_options(
        coverage_map_path=coverage_map_path,
        assume_coverage_complete=assume_coverage_complete,
        test_runner_template=test_runner_template,
    )

    cmd_value = coverage_map_cmd.strip() if coverage_map_cmd else None
    if cmd_value and coverage_map_path is None:
        raise ConfigError(
            "--coverage-map-cmd requires --coverage-map (the path the command "
            "writes the map to and PseudoClang reads it from)."
        )
    if refresh_coverage_map and not cmd_value:
        raise ConfigError(
            "--refresh-coverage-map requires --coverage-map-cmd (nothing to "
            "regenerate otherwise)."
        )

    if file is None or not file.strip():
        relative_file_path = None
        target_file = None
        if function is not None and function.strip():
            raise ConfigError("--function requires --file.")
        function_name = None
    else:
        relative_file_path = normalize_relative_file_path(file)
        target_file = resolve_target_file(root, relative_file_path)
        validate_target_file_exists(target_file)

        if function is None or not function.strip():
            function_name = None
            validate_sweep_file_extension(relative_file_path)
        else:
            function_name = _require_non_empty(function, "Function name")

    return PseudoScopeConfig(
        project_root=root,
        relative_file_path=relative_file_path,
        target_file=target_file,
        function_name=function_name,
        test_command=command,
        output_path=output_path,
        timeout_seconds=timeout_seconds,
        mode=mode_value,
        lang=lang_value,
        coverage_map_path=coverage_map_path,
        assume_coverage_complete=assume_coverage_complete,
        test_runner_template=template_value,
        coverage_map_cmd=cmd_value,
        refresh_coverage_map=refresh_coverage_map,
    )

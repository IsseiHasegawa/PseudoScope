"""Tests for pseudoclang.models (config dataclass)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from pseudoclang.models import ConfigError, PseudoScopeConfig


def _config(**overrides) -> PseudoScopeConfig:
    fields = dict(
        project_root=Path("/proj"),
        relative_file_path=Path("src/x.c"),
        target_file=Path("/proj/src/x.c"),
        function_name="f",
        test_command="pytest",
        output_path=Path("/proj/out.json"),
        timeout_seconds=60,
        mode=None,
        lang=None,
    )
    fields.update(overrides)
    return PseudoScopeConfig(**fields)


def test_fields_roundtrip():
    config = _config(function_name="add", timeout_seconds=5)
    assert config.function_name == "add"
    assert config.timeout_seconds == 5
    assert config.test_command == "pytest"
    assert config.project_root == Path("/proj")


def test_optional_fields_accept_none():
    config = _config(relative_file_path=None, target_file=None, mode=None, lang=None)
    assert config.target_file is None
    assert config.relative_file_path is None


def test_config_is_frozen():
    config = _config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.function_name = "other"  # type: ignore[misc]


def test_config_error_is_exception():
    assert issubclass(ConfigError, Exception)

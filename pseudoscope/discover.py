"""
Discover function definitions in C/C++ source via Tree-sitter.

Used for file-sweep mode. Body ranges use the same parser via ``locate``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pseudoscope.source import SourceFile
from pseudoscope.treesitter_util import (
    TreeSitterParseError,
    index_to_line,
    parsed_function_definitions,
)

SWEEP_SOURCE_SUFFIXES = frozenset({".c", ".cpp", ".cc", ".cxx"})


class DiscoverError(Exception):
    """Raised when functions cannot be discovered in a source file."""


@dataclass(frozen=True)
class DiscoveredFunction:
    """One function definition with a body, in source order."""

    name: str
    start_line: int
    start_byte: int
    end_byte: int


def validate_sweep_source_suffix(relative_path: Path) -> None:
    """Ensure the target file has a supported extension for file sweep."""
    suffix = relative_path.suffix.lower()
    if suffix not in SWEEP_SOURCE_SUFFIXES:
        supported = ", ".join(sorted(SWEEP_SOURCE_SUFFIXES))
        raise DiscoverError(
            f"File sweep supports {supported} only (got {relative_path.name!r})."
        )


def discover_functions(source: SourceFile) -> list[DiscoveredFunction]:
    """
    List all ``function_definition`` nodes with a body in ``source``.

    Raises :class:`DiscoverError` on unsupported extensions or parse failures.
    """
    validate_sweep_source_suffix(source.relative_path)

    try:
        definitions = parsed_function_definitions(source)
    except TreeSitterParseError as exc:
        raise DiscoverError(str(exc)) from exc

    discovered: list[DiscoveredFunction] = []
    for item in definitions:
        start_line = index_to_line(source.content, item.node.start_byte)
        discovered.append(
            DiscoveredFunction(
                name=item.name,
                start_line=start_line,
                start_byte=item.node.start_byte,
                end_byte=item.node.end_byte,
            )
        )
    return discovered

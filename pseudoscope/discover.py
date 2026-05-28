"""
Discover function definitions in C/C++ source via Tree-sitter.

Used for file-sweep mode only. Mutation location still uses ``locate``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Language, Node, Parser

from pseudoscope.source import SourceFile

SWEEP_SOURCE_SUFFIXES = frozenset({".c", ".cpp", ".cc", ".cxx"})

_IDENTIFIER_NODE_TYPES = frozenset(
    {
        "identifier",
        "field_identifier",
        "destructor_name",
        "operator_name",
    }
)

_DECLARATOR_NODE_TYPES = frozenset(
    {
        "function_declarator",
        "pointer_declarator",
        "reference_declarator",
        "array_declarator",
        "parenthesized_declarator",
        "declarator",
        "qualified_identifier",
    }
)


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


def _load_language(path: Path) -> Language:
    suffix = path.suffix.lower()
    if suffix == ".c":
        import tree_sitter_c as language_mod

        return Language(language_mod.language())
    if suffix in SWEEP_SOURCE_SUFFIXES - {".c"}:
        import tree_sitter_cpp as language_mod

        return Language(language_mod.language())
    raise DiscoverError(f"Unsupported source suffix for discovery: {suffix!r}")


def _identifier_from_node(node: Node) -> str | None:
    if node.type in _IDENTIFIER_NODE_TYPES:
        text = node.text
        if text is not None:
            return text.decode("utf-8")
    for child in node.children:
        found = _identifier_from_node(child)
        if found:
            return found
    return None


def _name_from_function_definition(node: Node) -> str | None:
    for child in node.children:
        if child.type == "compound_statement":
            continue
        if child.type in _DECLARATOR_NODE_TYPES or child.type == "identifier":
            name = _identifier_from_node(child)
            if name:
                return name
    return _identifier_from_node(node)


def _walk_function_definitions(root: Node) -> list[Node]:
    nodes: list[Node] = []
    stack = [root]
    while stack:
        current = stack.pop()
        if current.type == "function_definition":
            nodes.append(current)
        for child in reversed(current.children):
            if child.is_named:
                stack.append(child)
    nodes.sort(key=lambda item: item.start_byte)
    return nodes


def discover_functions(source: SourceFile) -> list[DiscoveredFunction]:
    """
    List all ``function_definition`` nodes with a body in ``source``.

    Raises :class:`DiscoverError` on unsupported extensions or parse failures.
    """
    validate_sweep_source_suffix(source.relative_path)

    try:
        language = _load_language(source.path)
        parser = Parser(language)
        tree = parser.parse(source.content.encode("utf-8"))
    except Exception as exc:
        raise DiscoverError(
            f"Failed to parse {source.relative_path} with Tree-sitter: {exc}"
        ) from exc

    discovered: list[DiscoveredFunction] = []
    for node in _walk_function_definitions(tree.root_node):
        if not any(child.type == "compound_statement" for child in node.children):
            continue
        name = _name_from_function_definition(node)
        if not name:
            continue
        start_line = source.content.count("\n", 0, node.start_byte) + 1
        discovered.append(
            DiscoveredFunction(
                name=name,
                start_line=start_line,
                start_byte=node.start_byte,
                end_byte=node.end_byte,
            )
        )
    return discovered

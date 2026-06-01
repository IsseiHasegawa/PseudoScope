"""
Shared Tree-sitter parsing for C/C++ function definitions.

Used by ``discover`` (list names) and ``locate`` (body byte ranges).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Language, Node, Parser, Tree

from pseudoscope.source import SourceFile

SUPPORTED_SOURCE_SUFFIXES = frozenset({".c", ".cpp", ".cc", ".cxx", ".h", ".hpp"})

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


class TreeSitterParseError(Exception):
    """Raised when a source file cannot be parsed with Tree-sitter."""


@dataclass(frozen=True)
class ParsedFunctionDefinition:
    """One function definition node and its extracted name."""

    name: str
    node: Node


def supports_treesitter(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES


def load_language(path: Path) -> Language:
    suffix = path.suffix.lower()
    if suffix == ".c":
        import tree_sitter_c as language_mod

        return Language(language_mod.language())
    if suffix in {".cpp", ".cc", ".cxx", ".hpp"}:
        import tree_sitter_cpp as language_mod

        return Language(language_mod.language())
    if suffix == ".h":
        import tree_sitter_c as language_mod

        return Language(language_mod.language())
    raise TreeSitterParseError(f"Unsupported source suffix for Tree-sitter: {suffix!r}")


def parse_source(source: SourceFile) -> Tree:
    try:
        language = load_language(source.path)
        parser = Parser(language)
        return parser.parse(source.content.encode("utf-8"))
    except TreeSitterParseError:
        raise
    except Exception as exc:
        raise TreeSitterParseError(
            f"Failed to parse {source.relative_path} with Tree-sitter: {exc}"
        ) from exc


def identifier_from_node(node: Node) -> str | None:
    if node.type in _IDENTIFIER_NODE_TYPES:
        text = node.text
        if text is not None:
            return text.decode("utf-8")
    for child in node.children:
        found = identifier_from_node(child)
        if found:
            return found
    return None


def name_from_function_definition(node: Node) -> str | None:
    for child in node.children:
        if child.type == "compound_statement":
            continue
        if child.type in _DECLARATOR_NODE_TYPES or child.type == "identifier":
            name = identifier_from_node(child)
            if name:
                return name
    return identifier_from_node(node)


def walk_function_definitions(root: Node) -> list[Node]:
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


def compound_statement_in_definition(node: Node) -> Node | None:
    for child in node.children:
        if child.type == "compound_statement":
            return child
    return None


def _top_level_declarator(defn: Node) -> Node | None:
    """Return the declarator child of a ``function_definition`` (before the body)."""
    for child in defn.children:
        if child.type == "compound_statement":
            return None
        if child.type == "comment":
            continue
        if child.type in _DECLARATOR_NODE_TYPES:
            return child
    return None


def _declarator_suffix(declarator: Node) -> str:
    """Collect leading ``*`` / ``&`` from nested declarators (e.g. ``char *name(...)``)."""
    suffix = ""
    node = declarator
    while node.type in ("pointer_declarator", "reference_declarator"):
        token = ""
        inner: Node | None = None
        for child in node.children:
            if child.type == "*":
                token = "*"
            elif child.type == "&":
                token = "&"
            elif child.type in _DECLARATOR_NODE_TYPES:
                inner = child
        suffix += token
        if inner is None:
            break
        node = inner
        if node.type == "function_declarator":
            break
    return suffix


def return_type_spelling_from_definition(defn: Node, content: str) -> str | None:
    """
    Extract return-type source text from a ``function_definition`` node.

    Combines type children before the declarator with ``*`` / ``&`` from
    ``pointer_declarator`` / ``reference_declarator`` chains.
    """
    declarator = _top_level_declarator(defn)
    if declarator is None:
        return None

    type_parts: list[str] = []
    for child in defn.children:
        if child.type == "compound_statement":
            break
        if child.type == "comment":
            continue
        if child is declarator:
            break
        part = content[child.start_byte : child.end_byte].strip()
        if part:
            type_parts.append(part)

    base = " ".join(type_parts)
    suffix = _declarator_suffix(declarator)
    if base and suffix:
        return f"{base}{suffix}"
    if base:
        return base
    if suffix:
        return suffix
    return None


def function_names_match(requested: str, discovered: str) -> bool:
    if requested == discovered:
        return True
    if "::" in requested:
        return requested.split("::")[-1] == discovered
    return False


def parsed_function_definitions(source: SourceFile) -> list[ParsedFunctionDefinition]:
    """Return all function definitions that have a body, in source order."""
    tree = parse_source(source)
    results: list[ParsedFunctionDefinition] = []
    for node in walk_function_definitions(tree.root_node):
        compound = compound_statement_in_definition(node)
        if compound is None:
            continue
        name = name_from_function_definition(node)
        if not name:
            continue
        results.append(ParsedFunctionDefinition(name=name, node=node))
    return results


def index_to_line(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def line_start_index(content: str, index: int) -> int:
    previous_newline = content.rfind("\n", 0, index)
    return 0 if previous_newline < 0 else previous_newline + 1

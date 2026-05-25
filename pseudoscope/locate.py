"""
Locate function and method bodies in C/C++ source (Step 3).

Finds byte ranges only; does not modify files, run tests, or write JSON.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pseudoscope.source import SourceFile


class FunctionLocateError(Exception):
    """Raised when the target function body cannot be located uniquely."""


@dataclass(frozen=True)
class FunctionBodyLocation:
    """Byte and line range of a located function body inside source text."""

    function_name: str
    path: Path
    relative_path: Path
    signature_start_index: int
    opening_brace_index: int
    body_start_index: int
    body_end_index: int
    closing_brace_index: int
    start_line: int
    end_line: int


def _index_to_line(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def _line_start_index(content: str, index: int) -> int:
    previous_newline = content.rfind("\n", 0, index)
    return 0 if previous_newline < 0 else previous_newline + 1


def _advance_past_string(content: str, index: int) -> int:
    quote = content[index]
    index += 1
    while index < len(content):
        if content[index] == "\\" and index + 1 < len(content):
            index += 2
            continue
        if content[index] == quote:
            return index + 1
        index += 1
    return len(content)


def _skip_whitespace_and_comments(content: str, index: int) -> int:
    while index < len(content):
        if content[index].isspace():
            index += 1
            continue
        if content[index : index + 2] == "//":
            while index < len(content) and content[index] != "\n":
                index += 1
            continue
        if content[index : index + 2] == "/*":
            index += 2
            while (
                index + 1 < len(content)
                and content[index : index + 2] != "*/"
            ):
                index += 1
            index = min(index + 2, len(content))
            continue
        break
    return index


def _find_matching_delimiter(
    content: str,
    open_index: int,
    *,
    open_char: str,
    close_char: str,
) -> int:
    depth = 0
    index = open_index
    length = len(content)

    while index < length:
        char = content[index]
        if char in ("'", '"'):
            index = _advance_past_string(content, index)
            continue
        if char == "/" and index + 1 < length:
            if content[index + 1] == "/":
                while index < length and content[index] != "\n":
                    index += 1
                continue
            if content[index + 1] == "*":
                index += 2
                while (
                    index + 1 < length
                    and content[index : index + 2] != "*/"
                ):
                    index += 1
                index = min(index + 2, length)
                continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index
        index += 1

    raise FunctionLocateError(
        f"Unbalanced '{open_char}' starting at index {open_index}."
    )


def _name_with_args_pattern(function_name: str) -> re.Pattern[str]:
    """
    Match ``name(`` only at an identifier boundary.

    Avoids false positives such as ``Dict_iterNext`` inside
    ``SortedDict_iterNext``.
    """
    escaped = re.escape(function_name)
    boundary = r"(?<![A-Za-z0-9_])"
    if "::" in function_name:
        return re.compile(rf"{boundary}{escaped}\s*\(")
    return re.compile(rf"{boundary}(?:[\w]+\s*::\s*)?{escaped}\s*\(")


def _function_name_start(match: re.Match[str], function_name: str) -> int:
    """Index of the function identifier within a ``name(`` regex match."""
    if "::" in function_name:
        return match.start()
    segment = match.group(0)
    local_name = function_name.split("::")[-1]
    return match.start() + segment.index(local_name)


def _try_locate_at_name(
    source: SourceFile,
    function_name: str,
    name_start: int,
    paren_open: int,
) -> FunctionBodyLocation | None:
    content = source.content

    try:
        paren_close = _find_matching_delimiter(
            content, paren_open, open_char="(", close_char=")"
        )
    except FunctionLocateError:
        return None

    index = _skip_whitespace_and_comments(content, paren_close + 1)
    if index >= len(content):
        return None

    if content[index] == ";":
        return None

    index = _skip_whitespace_and_comments(content, index)
    if index >= len(content) or content[index] == ";":
        return None
    if content[index] != "{":
        return None

    opening_brace_index = index

    try:
        closing_brace_index = _find_matching_delimiter(
            content,
            opening_brace_index,
            open_char="{",
            close_char="}",
        )
    except FunctionLocateError:
        return None

    # Half-open range: content[body_start_index:body_end_index]
    # represents everything inside the function braces.
    body_start_index = opening_brace_index + 1
    body_end_index = closing_brace_index

    signature_start_index = _line_start_index(content, name_start)

    return FunctionBodyLocation(
        function_name=function_name,
        path=source.path,
        relative_path=source.relative_path,
        signature_start_index=signature_start_index,
        opening_brace_index=opening_brace_index,
        body_start_index=body_start_index,
        body_end_index=body_end_index,
        closing_brace_index=closing_brace_index,
        start_line=_index_to_line(content, signature_start_index),
        end_line=_index_to_line(content, closing_brace_index),
    )


def locate_function_body(
    source: SourceFile,
    function_name: str,
) -> FunctionBodyLocation:
    """
    Locate the body of ``function_name`` in ``source.content``.

    Skips forward declarations ending with ``;``. Raises if zero or multiple
    definitions with bodies are found.
    """
    name = function_name.strip()
    if not name:
        raise FunctionLocateError("Function name must not be empty.")

    content = source.content
    pattern = _name_with_args_pattern(name)
    matches = list(pattern.finditer(content))

    if not matches:
        raise FunctionLocateError(
            f"No definition found for function '{name}' in {source.path}."
        )

    located: list[FunctionBodyLocation] = []
    for match in matches:
        name_start = _function_name_start(match, name)
        paren_open = content.find("(", name_start)
        if paren_open < 0:
            continue
        location = _try_locate_at_name(source, name, name_start, paren_open)
        if location is not None:
            located.append(location)

    if not located:
        raise FunctionLocateError(
            f"No function body found for '{name}' in {source.path} "
            "(only declarations or non-matching contexts)."
        )
    if len(located) > 1:
        lines = ", ".join(str(item.start_line) for item in located)
        raise FunctionLocateError(
            f"Ambiguous function '{name}' in {source.path}: "
            f"{len(located)} definitions found at lines {lines}."
        )

    return located[0]

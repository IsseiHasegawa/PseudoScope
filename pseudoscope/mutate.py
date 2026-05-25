"""
Generate default-return mutations in memory (Step 4).

Replaces the located function body with minimal return statements.
Does not write files, run tests, or produce JSON.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pseudoscope.locate import FunctionBodyLocation
from pseudoscope.source import SourceFile

MUTATION_TYPE_DEFAULT_RETURN = "replace_body_with_default_return"

ReturnTypeCategory = str  # void | bool | integer | float | double | string | char | pointer | fallback


class MutationError(Exception):
    """Raised when mutation generation fails."""


@dataclass(frozen=True)
class MutatedSource:
    """One in-memory mutation of a source file."""

    path: Path
    relative_path: Path
    function_name: str
    original_content: str
    mutated_content: str
    original_body: str
    replacement_body: str
    mutation_type: str
    return_type_category: ReturnTypeCategory
    body_start_index: int
    body_end_index: int


def _validate_body_range(
    content: str,
    location: FunctionBodyLocation,
) -> None:
    start = location.body_start_index
    end = location.body_end_index
    length = len(content)

    if start < 0 or end < 0 or start > end or end > length:
        raise MutationError(
            f"Invalid body range [{start}:{end}) for source length {length}."
        )
    if location.opening_brace_index >= length:
        raise MutationError("opening_brace_index is out of range.")
    if content[location.opening_brace_index] != "{":
        raise MutationError(
            f"Expected '{{' at opening_brace_index {location.opening_brace_index}, "
            f"found {content[location.opening_brace_index]!r}."
        )
    if location.closing_brace_index >= length:
        raise MutationError("closing_brace_index is out of range.")
    if content[location.closing_brace_index] != "}":
        raise MutationError(
            f"Expected '}}' at closing_brace_index {location.closing_brace_index}, "
            f"found {content[location.closing_brace_index]!r}."
        )
    if start != location.opening_brace_index + 1:
        raise MutationError(
            "body_start_index must equal opening_brace_index + 1 "
            f"(got {start}, opening brace at {location.opening_brace_index})."
        )
    if end != location.closing_brace_index:
        raise MutationError(
            "body_end_index must equal closing_brace_index "
            f"(got {end}, closing brace at {location.closing_brace_index})."
        )


def _extract_signature_text(
    source: SourceFile,
    location: FunctionBodyLocation,
) -> str:
    return source.content[
        location.signature_start_index : location.opening_brace_index
    ]


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_signature_prefixes(return_type: str) -> str:
    """Remove common C/C++ specifiers from the start of a return-type fragment."""
    cleaned = return_type
    prefix_pattern = re.compile(
        r"^(?:"
        r"static|inline|extern|virtual|constexpr|consteval|explicit|"
        r"friend|mutable|register|thread_local|"
        r"__inline__|__forceinline|__attribute__\s*\(\([^)]*\)\)"
        r")\s+",
        re.IGNORECASE,
    )
    while True:
        match = prefix_pattern.match(cleaned)
        if not match:
            break
        cleaned = cleaned[match.end() :].lstrip()
    return cleaned.strip()


def _return_type_from_signature(
    signature_text: str,
    function_name: str,
) -> str:
    sig = _collapse_whitespace(signature_text)
    local_name = function_name.split("::")[-1]

    if "::" in function_name:
        name_pattern = re.escape(function_name)
    else:
        name_pattern = rf"(?:[\w]+\s*::\s*)?{re.escape(local_name)}"

    match = re.search(rf"{name_pattern}\s*\(", sig)
    if not match:
        raise MutationError(
            f"Could not find function '{function_name}' in signature: {sig!r}"
        )

    return_type = sig[: match.start()].strip()
    return_type = _strip_signature_prefixes(return_type)
    if not return_type:
        raise MutationError(
            f"Could not infer return type from signature: {sig!r}"
        )
    return return_type


_INTEGER_TYPE_PATTERN = re.compile(
    r"(?:"
    r"unsigned\s+long\s+long|long\s+long|unsigned\s+long|unsigned\s+int|"
    r"unsigned\s+short|unsigned\s+char|"
    r"std::size_t|size_t|"
    r"int|short|long"
    r")\b",
    re.IGNORECASE,
)

_STL_CONTAINER_PATTERN = re.compile(
    r"\bstd::(?:"
    r"vector|map|set|list|deque|array|"
    r"unordered_map|unordered_set|"
    r"queue|stack|priority_queue|optional"
    r")\s*<",
    re.IGNORECASE,
)

def _infer_return_type_category(return_type: str) -> ReturnTypeCategory:
    normalized = _collapse_whitespace(return_type)
    lower = normalized.lower()

    if "*" in normalized:
        return "pointer"

    if re.search(r"\bvoid\b", lower):
        return "void"

    if re.search(r"\bbool\b", lower):
        return "bool"

    if re.search(r"(?:std::)?string\b", lower):
        return "string"

    if _STL_CONTAINER_PATTERN.search(normalized):
        return "fallback"

    if re.search(r"\bchar\b", lower):
        return "char"

    if re.search(r"\bfloat\b", lower) and "double" not in lower:
        return "float"

    if re.search(r"\bdouble\b", lower):
        return "double"

    if _INTEGER_TYPE_PATTERN.search(normalized):
        return "integer"

    return "fallback"


def _replacement_bodies_for_category(
    category: ReturnTypeCategory,
) -> list[str]:
    if category == "void":
        return ["\n    return;\n"]
    if category == "bool":
        return ["\n    return false;\n", "\n    return true;\n"]
    if category == "integer":
        return ["\n    return 0;\n", "\n    return 1;\n"]
    if category == "float":
        return ["\n    return 0.0f;\n", "\n    return 1.0f;\n"]
    if category == "double":
        return ["\n    return 0.0;\n", "\n    return 1.0;\n"]
    if category == "string":
        return ['\n    return "";\n', '\n    return "A";\n']
    if category == "char":
        return ["\n    return '\\0';\n", "\n    return 'a';\n"]
    if category == "pointer":
        return ["\n    return nullptr;\n"]
    return ["\n    return {};\n"]


def replacement_return_line(replacement_body: str) -> str:
    for line in replacement_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("return"):
            return stripped
    return replacement_body.strip()


def generate_default_return_mutations(
    source: SourceFile,
    location: FunctionBodyLocation,
) -> list[MutatedSource]:
    """
    Build in-memory source variants with default-return bodies.

    Raises :class:`MutationError` if the body range or signature is invalid.
    """
    _validate_body_range(source.content, location)

    body_start = location.body_start_index
    body_end = location.body_end_index
    original_body = source.content[body_start:body_end]

    signature_text = _extract_signature_text(source, location)
    return_type = _return_type_from_signature(signature_text, location.function_name)
    category = _infer_return_type_category(return_type)
    replacements = _replacement_bodies_for_category(category)

    mutations: list[MutatedSource] = []
    for replacement_body in replacements:
        mutated_content = (
            source.content[:body_start]
            + replacement_body
            + source.content[body_end:]
        )
        mutations.append(
            MutatedSource(
                path=source.path,
                relative_path=source.relative_path,
                function_name=location.function_name,
                original_content=source.content,
                mutated_content=mutated_content,
                original_body=original_body,
                replacement_body=replacement_body,
                mutation_type=MUTATION_TYPE_DEFAULT_RETURN,
                return_type_category=category,
                body_start_index=body_start,
                body_end_index=body_end,
            )
        )

    return mutations

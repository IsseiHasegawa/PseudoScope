"""
Generate default-return mutations in memory (Step 4).

Replaces the located function body with minimal return statements.
Return types come from Tree-sitter when ``FunctionBodyLocation.return_type_spelling``
is set (via ``locate``); otherwise signature text is parsed with regex.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pseudoclang.locate import FunctionBodyLocation
from pseudoclang.source import SourceFile
from pseudoclang.treesitter_util import source_language

MUTATION_TYPE_DEFAULT_RETURN = "replace_body_with_default_return"

# void | bool | integer | float | double | string | char | pointer | fallback
# | reference | unresolved
ReturnTypeCategory = str

#: Categories with no safe default-return mutation: a reference has no bindable
#: default, and an unresolved ``auto`` / ``decltype(auto)`` cannot be deduced
#: without a full type checker. The operator skips and labels these.
EXCLUDED_CATEGORIES = frozenset({"reference", "unresolved"})


class MutationError(Exception):
    """Raised when mutation generation fails."""


class UnsupportedReturnTypeError(MutationError):
    """Return type has no safe default-return mutation (reference / unresolved auto).

    Subclasses :class:`MutationError` so existing ``except MutationError`` callers
    still degrade gracefully; callers that want a distinct label catch this first.
    """

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.category = category


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
    r"unsigned|signed|"
    r"int|short|long"
    r")\b",
    re.IGNORECASE,
)

#: ``auto`` / ``decltype(auto)`` placeholder return type (after stripping a
#: leading ``const`` / ``volatile``) with no trailing-return type to resolve it.
_PLACEHOLDER_TYPE_PATTERN = re.compile(
    r"^(?:const\s+|volatile\s+)*(?:auto|decltype\s*\(\s*auto\s*\))$",
    re.IGNORECASE,
)

#: ``#include <stdbool.h>`` (or ``"stdbool.h"``) anywhere in the translation unit.
_STDBOOL_INCLUDE_PATTERN = re.compile(
    r'^[ \t]*#[ \t]*include[ \t]*[<"]stdbool\.h[>"]',
    re.MULTILINE,
)


def _has_top_level_marker(text: str, markers: str) -> bool:
    """True if any character in ``markers`` appears at angle-bracket depth 0.

    A ``*`` / ``&`` only makes a return type a pointer / reference when it sits
    outside every ``<...>`` template argument: ``std::vector<int>*`` is a pointer,
    but the ``*`` in ``std::vector<int*>`` (and the ``&`` in
    ``std::function<void(int&)>``) belongs to a template argument and must not
    reclassify the whole type. Depth is clamped at zero so a stray ``>`` cannot
    drive it negative.
    """
    depth = 0
    for char in text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif depth == 0 and char in markers:
            return True
    return False


def _infer_return_type_category(return_type: str) -> ReturnTypeCategory:
    normalized = _collapse_whitespace(return_type)
    lower = normalized.lower()

    # decltype(<expr>) cannot be resolved without a type checker (and a '&' inside
    # the expression must not be read as a reference marker); exclude and label it.
    if "decltype(" in lower:
        return "unresolved"

    # References: a top-level '&' (incl. 'T*&' and rvalue 'T&&') means a reference
    # return, which this operator excludes (no bindable default). A '&' inside a
    # template argument (e.g. std::function<void(int&)>) is not a reference return.
    if _has_top_level_marker(normalized, "&"):
        return "reference"

    # A top-level '*' is a pointer return ('int *', 'std::vector<int>*'); a '*'
    # only inside '<...>' (e.g. std::vector<int*>) is a template arg, not a pointer.
    if _has_top_level_marker(normalized, "*"):
        return "pointer"

    # Bare 'auto' that survived trailing-return resolution cannot be deduced
    # without a type checker; exclude and label it.
    if _PLACEHOLDER_TYPE_PATTERN.match(normalized):
        return "unresolved"

    if re.fullmatch(r"(?:const\s+|volatile\s+)*void", lower):
        return "void"

    # Any templated/generic type ('<...>'): STL container, smart ptr, optional,
    # std::function, or a user template -- all take '{}' in C++. Checked before the
    # scalar keywords so a template argument (e.g. vector<bool>, tuple<int>) is not
    # mistaken for the return type itself.
    if "<" in normalized:
        return "fallback"

    if re.search(r"\bbool\b", lower):
        return "bool"

    if re.search(r"\b(?:std::)?string\b", lower):
        return "string"

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
    *,
    return_type: str,
    is_cpp: bool,
    has_stdbool: bool,
) -> list[str]:
    """Return-statement variants for ``category`` in the C++ or C column.

    Two distinct variants are emitted wherever two safe values exist (so a mutant
    cannot accidentally equal the original return value). Pointers and opaque
    aggregate types stay single-valued.
    """
    if category == "void":
        return ["\n"]  # empty body, no return value
    if category == "bool":
        # C without <stdbool.h> has no false/true keywords; fall back to 0/1.
        if is_cpp or has_stdbool:
            return ["\n    return false;\n", "\n    return true;\n"]
        return ["\n    return 0;\n", "\n    return 1;\n"]
    if category == "integer":
        return ["\n    return 0;\n", "\n    return 1;\n"]
    if category in ("float", "double"):
        return ["\n    return 0.0;\n", "\n    return 1.0;\n"]
    if category == "string":
        if is_cpp:
            return ['\n    return "";\n', '\n    return "A";\n']
        # No std::string in C; treat as a pointer (single null variant).
        return ["\n    return NULL;\n"]
    if category == "char":
        return ["\n    return '\\0';\n", "\n    return 'a';\n"]
    if category == "pointer":
        return ["\n    return nullptr;\n"] if is_cpp else ["\n    return NULL;\n"]
    # fallback: class / struct / enum / template / STL / smart ptr / optional.
    if is_cpp:
        return ["\n    return {};\n"]  # {} for default-constructible / aggregate
    # C aggregates: enums take 0; everything else a zero compound literal.
    if re.search(r"\benum\b", return_type.lower()):
        return ["\n    return 0;\n"]
    return [f"\n    return ({return_type}){{0}};\n"]


def replacement_return_line(replacement_body: str) -> str:
    for line in replacement_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("return"):
            return stripped
    stripped = replacement_body.strip()
    return stripped if stripped else "(empty body)"


def _split_signature_around_params(
    source: SourceFile,
    location: FunctionBodyLocation,
) -> tuple[str | None, str | None]:
    """Split the signature around the function's parameter list.

    Returns ``(return_head, after_params)``: the text before the function name's
    ``(`` and the text after its matching ``)``. Either is ``None`` when the
    parameter list cannot be located. Anchoring on the name's parameter list keeps a
    ``->`` inside ``operator->`` or a default argument from being mistaken for a
    trailing-return arrow.
    """
    sig = _collapse_whitespace(_extract_signature_text(source, location))
    local_name = location.function_name.split("::")[-1]
    match = re.search(rf"(?<![\w]){re.escape(local_name)}\s*\(", sig)
    if not match:
        return None, None

    head = sig[: match.start()]
    depth = 0
    index = match.end() - 1
    while index < len(sig):
        char = sig[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return head, sig[index + 1 :]
        index += 1
    return head, None


def _reconcile_with_signature(
    return_type: str,
    source: SourceFile,
    location: FunctionBodyLocation,
) -> str:
    """Recover reference / trailing-return info the Tree-sitter spelling can drop.

    The spelling loses ``&`` on ``const T&`` (-> ``const``), ``&&`` on ``int&&``
    (-> ``int``), and the ``-> T`` of a trailing-return ``auto f() -> T``. The raw
    signature text keeps them, so consult it for exactly those cases.
    """
    head, after = _split_signature_around_params(source, location)

    if _PLACEHOLDER_TYPE_PATTERN.match(return_type):
        if after:
            match = re.search(r"->\s*(.+?)\s*$", after)
            if match:
                return _strip_signature_prefixes(match.group(1).strip())
        return return_type  # bare auto / decltype(auto): left unresolved

    if "&" not in return_type and head is not None and "&" in head:
        return f"{return_type}&"
    return return_type


def _resolve_return_type(
    source: SourceFile,
    location: FunctionBodyLocation,
) -> str:
    if location.return_type_spelling:
        return_type = _strip_signature_prefixes(
            _collapse_whitespace(location.return_type_spelling)
        )
    else:
        signature_text = _extract_signature_text(source, location)
        return_type = _return_type_from_signature(
            signature_text, location.function_name
        )

    return_type = _reconcile_with_signature(return_type, source, location)

    if not return_type:
        raise MutationError(
            f"Could not infer return type for '{location.function_name}' "
            f"in {source.path}."
        )
    return return_type


def resolve_return_type_category(
    source: SourceFile,
    location: FunctionBodyLocation,
) -> ReturnTypeCategory:
    """Resolve the mutation category for a located function (for tests and tooling)."""
    return _infer_return_type_category(_resolve_return_type(source, location))


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

    return_type = _resolve_return_type(source, location)
    category = _infer_return_type_category(return_type)
    if category in EXCLUDED_CATEGORIES:
        raise UnsupportedReturnTypeError(
            f"Return type {return_type!r} for '{location.function_name}' has no "
            f"safe default-return mutation ({category}); excluded from this operator.",
            category=category,
        )

    is_cpp = source_language(source.path) == "cpp"
    has_stdbool = is_cpp or bool(_STDBOOL_INCLUDE_PATTERN.search(source.content))
    replacements = _replacement_bodies_for_category(
        category,
        return_type=return_type,
        is_cpp=is_cpp,
        has_stdbool=has_stdbool,
    )

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

"""
PseudoScope — detect pseudo-tested code in C/C++ projects.

Steps 1–5 (current): validate CLI input, read source, locate function body,
generate mutations in memory, and write/restore source on disk.
"""

from pseudoscope.locate import (
    FunctionBodyLocation,
    FunctionLocateError,
    locate_function_body,
)
from pseudoscope.models import ConfigError, PseudoScopeConfig
from pseudoscope.mutate import (
    MutationError,
    MutatedSource,
    generate_default_return_mutations,
)
from pseudoscope.source import SourceFile, SourceReadError, read_source_file
from pseudoscope.validation import build_config
from pseudoscope.workspace import (
    WorkspaceError,
    restore_original_source,
    write_mutated_source,
)

__version__ = "0.1.0"

__all__ = [
    "ConfigError",
    "FunctionBodyLocation",
    "FunctionLocateError",
    "MutationError",
    "MutatedSource",
    "PseudoScopeConfig",
    "SourceFile",
    "SourceReadError",
    "WorkspaceError",
    "build_config",
    "generate_default_return_mutations",
    "locate_function_body",
    "read_source_file",
    "restore_original_source",
    "write_mutated_source",
    "__version__",
]

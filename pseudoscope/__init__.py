"""
PseudoScope — detect pseudo-tested code in C/C++ projects.

Steps 1–4 (current): validate CLI input, read source, locate function body,
and generate default-return mutations in memory.
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
    "build_config",
    "generate_default_return_mutations",
    "locate_function_body",
    "read_source_file",
    "__version__",
]

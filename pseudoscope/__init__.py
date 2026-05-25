"""
PseudoScope — detect pseudo-tested code in C/C++ projects.

Steps 1–2 (current): validate CLI input and read the target source file.
"""

from pseudoscope.models import ConfigError, PseudoScopeConfig
from pseudoscope.source import SourceFile, SourceReadError, read_source_file
from pseudoscope.validation import build_config

__version__ = "0.1.0"

__all__ = [
    "ConfigError",
    "PseudoScopeConfig",
    "SourceFile",
    "SourceReadError",
    "build_config",
    "read_source_file",
    "__version__",
]

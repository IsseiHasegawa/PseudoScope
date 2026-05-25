"""
PseudoScope — detect pseudo-tested code in C/C++ projects.

Step 1 (current): parse, normalize, and validate CLI input only.
"""

from pseudoscope.models import ConfigError, PseudoScopeConfig
from pseudoscope.validation import build_config

__version__ = "0.1.0"

__all__ = [
    "ConfigError",
    "PseudoScopeConfig",
    "build_config",
    "__version__",
]

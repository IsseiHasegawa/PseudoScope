"""
PseudoScope — detect pseudo-tested code in C/C++ projects.

Steps 1–7 (current): validate CLI input, read source, locate function body,
generate mutations, write/restore on disk, run baseline and mutation tests.
"""

from pseudoscope.executor import (
    MutationExecutionError,
    MutationRunResult,
    run_mutation_tests,
)
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
from pseudoscope.runner import TestRunError, TestRunResult, run_test_command
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
    "MutationExecutionError",
    "MutationRunResult",
    "MutatedSource",
    "PseudoScopeConfig",
    "SourceFile",
    "SourceReadError",
    "TestRunError",
    "TestRunResult",
    "WorkspaceError",
    "build_config",
    "generate_default_return_mutations",
    "locate_function_body",
    "read_source_file",
    "restore_original_source",
    "run_mutation_tests",
    "run_test_command",
    "write_mutated_source",
    "__version__",
]

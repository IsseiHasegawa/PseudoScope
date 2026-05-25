"""
PseudoScope — detect pseudo-tested code in C/C++ projects.

Steps 1–8 (current): full single-function analysis pipeline including JSON output.
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
from pseudoscope.results import (
    ResultWriteError,
    build_function_analysis_result,
    classify_function,
    write_json_result,
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
    "ResultWriteError",
    "SourceFile",
    "SourceReadError",
    "TestRunError",
    "TestRunResult",
    "WorkspaceError",
    "build_config",
    "build_function_analysis_result",
    "classify_function",
    "generate_default_return_mutations",
    "locate_function_body",
    "read_source_file",
    "restore_original_source",
    "run_mutation_tests",
    "run_test_command",
    "write_json_result",
    "write_mutated_source",
    "__version__",
]

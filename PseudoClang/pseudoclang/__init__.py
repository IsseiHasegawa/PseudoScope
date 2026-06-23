"""
PseudoScope — detect pseudo-tested code in C/C++ projects.

Steps 1–8 (current): full single-function analysis pipeline including JSON output.
"""

from pseudoclang.executor import (
    MutationExecutionError,
    MutationRunResult,
    run_mutation_tests,
)
from pseudoclang.locate import (
    FunctionBodyLocation,
    FunctionLocateError,
    locate_function_body,
)
from pseudoclang.models import ConfigError, PseudoScopeConfig
from pseudoclang.mutate import (
    MutationError,
    MutatedSource,
    generate_default_return_mutations,
)
from pseudoclang.results import (
    ResultWriteError,
    build_function_analysis_result,
    build_result_table_rows,
    classify_function,
    display_status,
    write_json_result,
)
from pseudoclang.runner import TestRunError, TestRunResult, run_test_command
from pseudoclang.source import SourceFile, SourceReadError, read_source_file
from pseudoclang.validation import build_config
from pseudoclang.workspace import (
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
    "build_result_table_rows",
    "classify_function",
    "display_status",
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

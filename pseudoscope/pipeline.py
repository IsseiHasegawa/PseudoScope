"""
Planned analysis pipeline.

Step 1 (implemented): ``cli`` + ``validation`` — parse and validate input.
Step 2 (implemented): ``source`` — read the target file from disk.
Step 3 (implemented): ``locate`` — find the function/method body range.
Step 4 (implemented): ``mutate`` — generate default-return mutations in memory.
Step 5 (implemented): ``workspace`` — write mutated source to disk and restore
the original (pair write with restore in a ``finally`` block).
Step 6 (implemented): ``runner`` — run the test command and capture results.
Step 7 (implemented): ``executor`` — execute mutation tests using
write → run → restore for each mutation.

Future steps (not implemented yet):

  write JSON results

The CLI entry point should eventually orchestrate those stages using a
:class:`~pseudoscope.models.PseudoScopeConfig` instance produced here.
"""

from __future__ import annotations

STEP_VALIDATE_INPUT = "validate_input"
STEP_READ_SOURCE = "read_source"
STEP_LOCATE_FUNCTION = "locate_function"
STEP_GENERATE_MUTATIONS = "generate_mutations"
STEP_WRITE_MUTATED_SOURCE = "write_mutated_source"
STEP_RUN_TESTS = "run_tests"
STEP_EXECUTE_MUTATION_TESTS = "execute_mutation_tests"
STEP_RESTORE_SOURCE = "restore_source"
STEP_WRITE_RESULTS = "write_results"

PIPELINE_STEPS: tuple[str, ...] = (
    STEP_VALIDATE_INPUT,
    STEP_READ_SOURCE,
    STEP_LOCATE_FUNCTION,
    STEP_GENERATE_MUTATIONS,
    STEP_WRITE_MUTATED_SOURCE,
    STEP_RUN_TESTS,
    STEP_EXECUTE_MUTATION_TESTS,
    STEP_RESTORE_SOURCE,
    STEP_WRITE_RESULTS,
)

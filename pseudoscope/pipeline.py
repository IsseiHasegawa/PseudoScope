"""
Planned analysis pipeline (documentation only for Step 1).

Step 1 (implemented): ``cli`` + ``validation`` — parse and validate input.

Future steps (not implemented yet) will live in dedicated modules, for example:

  source.py     — read the target file from disk
  locate.py     — find the function/method body (e.g. Tree-sitter)
  mutate.py     — delete/replace the body; backup and restore originals
  runner.py     — execute ``test_command`` under ``project_root`` with timeout
  results.py    — build and write structured JSON to ``output_path``

The CLI entry point should eventually orchestrate those stages using a
:class:`~pseudoscope.models.PseudoScopeConfig` instance produced here.
"""

from __future__ import annotations

# Step identifiers for future orchestration (no runtime behavior in Step 1).
STEP_VALIDATE_INPUT = "validate_input"
STEP_READ_SOURCE = "read_source"
STEP_LOCATE_FUNCTION = "locate_function"
STEP_DELETE_BODY = "delete_body"
STEP_RUN_TESTS = "run_tests"
STEP_RESTORE_SOURCE = "restore_source"
STEP_WRITE_RESULTS = "write_results"

PIPELINE_STEPS: tuple[str, ...] = (
    STEP_VALIDATE_INPUT,
    STEP_READ_SOURCE,
    STEP_LOCATE_FUNCTION,
    STEP_DELETE_BODY,
    STEP_RUN_TESTS,
    STEP_RESTORE_SOURCE,
    STEP_WRITE_RESULTS,
)

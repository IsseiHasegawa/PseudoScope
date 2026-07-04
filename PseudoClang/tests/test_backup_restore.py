"""Crash-safety backups and the ``restore`` command (Phase C).

Backups let a *separate* process undo a mutation a hard crash (SIGKILL / power
loss) left on disk, so the target project can always be returned to original.
The autouse ``_isolate_backups`` fixture (conftest) points the safety net at a
temp dir, so these never touch the real output/backups.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pseudoclang import backup
from pseudoclang.cli import main
from pseudoclang.restore_backstop import guarded_source_write


def _crash(target: Path, backups: Path, original: bytes, mutated: bytes) -> None:
    """Simulate a run killed mid-mutation: mutant on disk, backup recorded, no clear."""
    target.write_bytes(mutated)
    backup.record(
        target, original_bytes=original, mutated_bytes=mutated,
        function="fn", backups_dir=backups,
    )


# --- backup layer lifecycle -------------------------------------------------

def test_clean_context_leaves_no_backup(tmp_path):
    f = tmp_path / "x.c"
    f.write_text("ORIG\n")
    with guarded_source_write(f, "NEW\n", "ORIG\n"):
        pass  # normal completion restores and clears
    # Nothing pending anywhere (uses the isolated default dir).
    assert backup.restore_pending() == []
    assert f.read_text() == "ORIG\n"


def test_record_then_clear_removes_entry_and_file(tmp_path):
    backups = tmp_path / "b"
    f = tmp_path / "x.c"
    backup.record(f, original_bytes=b"o", mutated_bytes=b"m", backups_dir=backups)
    assert backup.restore_pending(backups_dir=backups, dry_run=True)  # one pending
    backup.clear(f, backups_dir=backups)
    assert backup.restore_pending(backups_dir=backups) == []


# --- restore of a crashed mutation ------------------------------------------

def test_restore_reverts_our_mutant(tmp_path):
    backups = tmp_path / "b"
    f = tmp_path / "x.c"
    _crash(f, backups, original=b"ORIG\n", mutated=b"MUT\n")

    outcomes = backup.restore_pending(backups_dir=backups)

    assert [o.status for o in outcomes] == ["restored"]
    assert f.read_bytes() == b"ORIG\n"
    # Entry cleared -> a second restore has nothing to do.
    assert backup.restore_pending(backups_dir=backups) == []


def test_restore_preserves_bytes_exactly_including_crlf(tmp_path):
    backups = tmp_path / "b"
    f = tmp_path / "x.c"
    original = b"a\r\nb\r\n"  # CRLF must survive byte-for-byte
    _crash(f, backups, original=original, mutated=b"a\r\nZ\r\n")

    backup.restore_pending(backups_dir=backups)
    assert f.read_bytes() == original


def test_already_clean_clears_without_writing(tmp_path):
    backups = tmp_path / "b"
    f = tmp_path / "x.c"
    _crash(f, backups, original=b"ORIG\n", mutated=b"MUT\n")
    f.write_bytes(b"ORIG\n")  # something already put it back

    outcomes = backup.restore_pending(backups_dir=backups)

    assert [o.status for o in outcomes] == ["already_clean"]
    assert backup.restore_pending(backups_dir=backups) == []


# --- the user-edit guard ----------------------------------------------------

def test_user_edit_is_skipped_not_clobbered(tmp_path):
    backups = tmp_path / "b"
    f = tmp_path / "x.c"
    _crash(f, backups, original=b"ORIG\n", mutated=b"MUT\n")
    f.write_bytes(b"USER EDIT\n")  # neither original nor our mutant

    outcomes = backup.restore_pending(backups_dir=backups)

    assert [o.status for o in outcomes] == ["skipped_user_edit"]
    assert f.read_bytes() == b"USER EDIT\n"  # untouched
    # Entry remains pending so the user can decide (e.g. --force later).
    assert len(backup.restore_pending(backups_dir=backups, dry_run=True)) == 1


def test_force_overwrites_user_edit(tmp_path):
    backups = tmp_path / "b"
    f = tmp_path / "x.c"
    _crash(f, backups, original=b"ORIG\n", mutated=b"MUT\n")
    f.write_bytes(b"USER EDIT\n")

    outcomes = backup.restore_pending(backups_dir=backups, force=True)

    assert [o.status for o in outcomes] == ["forced"]
    assert f.read_bytes() == b"ORIG\n"


# --- dry-run and scoping ----------------------------------------------------

def test_dry_run_reports_without_writing_or_clearing(tmp_path):
    backups = tmp_path / "b"
    f = tmp_path / "x.c"
    _crash(f, backups, original=b"ORIG\n", mutated=b"MUT\n")

    outcomes = backup.restore_pending(backups_dir=backups, dry_run=True)

    assert [o.status for o in outcomes] == ["restored"]
    assert f.read_bytes() == b"MUT\n"  # not written
    assert len(backup.restore_pending(backups_dir=backups, dry_run=True)) == 1  # not cleared


def test_project_root_scopes_which_files_restore(tmp_path):
    backups = tmp_path / "b"
    root_a = tmp_path / "a"
    root_b = tmp_path / "c"
    (root_a).mkdir()
    (root_b).mkdir()
    fa = root_a / "x.c"
    fb = root_b / "y.c"
    _crash(fa, backups, original=b"A\n", mutated=b"MA\n")
    _crash(fb, backups, original=b"B\n", mutated=b"MB\n")

    outcomes = backup.restore_pending(backups_dir=backups, project_root=root_a)

    assert [o.status for o in outcomes] == ["restored"]
    assert fa.read_bytes() == b"A\n"
    assert fb.read_bytes() == b"MB\n"  # outside the scope, left alone
    # fb still pending.
    assert len(backup.restore_pending(backups_dir=backups, dry_run=True)) == 1


# --- the restore CLI --------------------------------------------------------

def test_restore_cli_reverts_and_is_idempotent(tmp_path):
    # Uses the default (env-isolated) backups dir shared with backup.record.
    f = tmp_path / "proj" / "x.c"
    f.parent.mkdir()
    _crash(f, backup.default_backups_dir(), original=b"ORIG\n", mutated=b"MUT\n")

    assert main(["restore"]) == 0
    assert f.read_bytes() == b"ORIG\n"
    # Nothing left to do the second time.
    assert main(["restore"]) == 0


def test_restore_cli_returns_nonzero_when_user_edit_blocks(tmp_path):
    f = tmp_path / "x.c"
    _crash(f, backup.default_backups_dir(), original=b"ORIG\n", mutated=b"MUT\n")
    f.write_bytes(b"USER\n")

    assert main(["restore"]) == 1  # something remains un-restored
    assert f.read_bytes() == b"USER\n"
    assert main(["restore", "--force"]) == 0
    assert f.read_bytes() == b"ORIG\n"

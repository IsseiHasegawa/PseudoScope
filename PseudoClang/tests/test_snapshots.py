"""Recovery-point snapshot history: capture, keep-last-N retention, rollback.

Each run copies the pristine source into its own snapshot directory before
mutating it; the oldest are pruned so only the most recent ``max_snapshots``
remain, giving a bounded, rollback-able history of past recovery points.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from pseudoclang import snapshots


def _at(second: int) -> datetime:
    """A fixed timestamp (varying only the second) for deterministic dir names."""
    return datetime(2026, 7, 13, 12, 0, second)


def _make(snaps_dir, content: bytes, target, *, seq_second: int, max_snapshots=5):
    return snapshots.create_snapshot(
        [(target, content)],
        label=str(target),
        max_snapshots=max_snapshots,
        snapshots_dir=snaps_dir,
        now=_at(seq_second),
    )


# --- capture ----------------------------------------------------------------

def test_create_writes_snapshot_with_manifest(tmp_path):
    snaps = tmp_path / "snaps"
    target = tmp_path / "x.c"
    info = _make(snaps, b"ORIGINAL\n", target, seq_second=1)

    assert info is not None
    assert info.sequence == 1
    assert info.path.is_dir()
    assert (info.path / snapshots.MANIFEST_NAME).is_file()
    assert len(info.files) == 1
    backup_file = info.path / info.files[0].backup
    assert backup_file.read_bytes() == b"ORIGINAL\n"


def test_sequence_increments_per_snapshot(tmp_path):
    snaps = tmp_path / "snaps"
    target = tmp_path / "x.c"
    a = _make(snaps, b"v1\n", target, seq_second=1)
    b = _make(snaps, b"v2\n", target, seq_second=2)
    assert (a.sequence, b.sequence) == (1, 2)


def test_disabled_when_max_is_zero(tmp_path):
    snaps = tmp_path / "snaps"
    target = tmp_path / "x.c"
    assert _make(snaps, b"v1\n", target, seq_second=1, max_snapshots=0) is None
    assert snapshots.list_snapshots(snaps) == []


def test_identical_content_is_not_re_snapshotted(tmp_path):
    snaps = tmp_path / "snaps"
    target = tmp_path / "x.c"
    first = _make(snaps, b"same\n", target, seq_second=1)
    again = _make(snaps, b"same\n", target, seq_second=2)
    # Dedup: the second call returns the existing latest, no new dir is written.
    assert again.sequence == first.sequence
    assert len(snapshots.list_snapshots(snaps)) == 1


# --- retention (the friend's cleanup) ---------------------------------------

def test_keeps_only_the_last_n_and_deletes_oldest(tmp_path):
    snaps = tmp_path / "snaps"
    target = tmp_path / "x.c"
    for i in range(1, 8):  # seven distinct recovery points
        _make(snaps, f"v{i}\n".encode(), target, seq_second=i, max_snapshots=5)

    kept = snapshots.list_snapshots(snaps)
    assert [s.sequence for s in kept] == [3, 4, 5, 6, 7]  # oldest two pruned


# --- listing ----------------------------------------------------------------

def test_list_is_ordered_oldest_first(tmp_path):
    snaps = tmp_path / "snaps"
    target = tmp_path / "x.c"
    _make(snaps, b"v1\n", target, seq_second=1)
    _make(snaps, b"v2\n", target, seq_second=2)
    seqs = [s.sequence for s in snapshots.list_snapshots(snaps)]
    assert seqs == sorted(seqs)


# --- rollback ---------------------------------------------------------------

def test_restore_rolls_a_changed_file_back(tmp_path):
    snaps = tmp_path / "snaps"
    target = tmp_path / "x.c"
    target.write_bytes(b"ORIGINAL\n")
    info = _make(snaps, b"ORIGINAL\n", target, seq_second=1)

    target.write_bytes(b"EDITED LATER\n")  # user moved on
    outcomes = snapshots.restore_snapshot(info.sequence, snapshots_dir=snaps)

    assert [o.status for o in outcomes] == ["restored"]
    assert target.read_bytes() == b"ORIGINAL\n"


def test_restore_already_matching_is_a_noop(tmp_path):
    snaps = tmp_path / "snaps"
    target = tmp_path / "x.c"
    target.write_bytes(b"ORIGINAL\n")
    info = _make(snaps, b"ORIGINAL\n", target, seq_second=1)

    outcomes = snapshots.restore_snapshot(info.sequence, snapshots_dir=snaps)
    assert [o.status for o in outcomes] == ["already_clean"]


def test_restore_dry_run_does_not_write(tmp_path):
    snaps = tmp_path / "snaps"
    target = tmp_path / "x.c"
    target.write_bytes(b"ORIGINAL\n")
    info = _make(snaps, b"ORIGINAL\n", target, seq_second=1)
    target.write_bytes(b"EDITED\n")

    outcomes = snapshots.restore_snapshot(
        info.sequence, dry_run=True, snapshots_dir=snaps
    )
    assert [o.status for o in outcomes] == ["restored"]
    assert target.read_bytes() == b"EDITED\n"  # untouched


def test_restore_unknown_sequence_returns_empty(tmp_path):
    snaps = tmp_path / "snaps"
    assert snapshots.restore_snapshot(99, snapshots_dir=snaps) == []


# --- retention count resolution ---------------------------------------------

@pytest.mark.parametrize(
    "value, env, expected",
    [
        (3, None, 3),
        (None, None, snapshots.DEFAULT_MAX_SNAPSHOTS),
        (None, "2", 2),
        (None, "garbage", snapshots.DEFAULT_MAX_SNAPSHOTS),
        (-4, None, 0),
    ],
)
def test_resolve_max_snapshots(value, env, expected, monkeypatch):
    if env is None:
        monkeypatch.delenv(snapshots.MAX_SNAPSHOTS_ENV, raising=False)
    else:
        monkeypatch.setenv(snapshots.MAX_SNAPSHOTS_ENV, env)
    assert snapshots.resolve_max_snapshots(value) == expected

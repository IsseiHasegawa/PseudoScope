"""
Recovery-point history: keep the last N pre-mutation source snapshots.

The crash-safety net in :mod:`pseudoclang.backup` clears each backup the moment
its source is restored, so a clean run leaves nothing behind and there is no way
to roll a source tree back to how it looked before an *earlier* run. This module
adds that history. Before a run mutates a file, the pristine original is copied
into its own timestamped snapshot directory under ``output/snapshots``. Each run
adds one snapshot; when the number of snapshots exceeds ``max_snapshots``
(``--max-snapshots``, default 5) the oldest are deleted, so a bounded history of
the most recent recovery points is retained.

A snapshot is self-describing (each carries its own ``snapshot.json``) and is
independent of the backup manifest, so ``pseudoclang snapshots`` can list the
history and ``pseudoclang restore --snapshot N`` can roll the captured files
back to that point.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pseudoclang.atomicio import atomic_write_bytes
from pseudoclang.backup import RestoreOutcome
from pseudoclang.validation import default_output_dir

MANIFEST_NAME = "snapshot.json"
SNAPSHOT_VERSION = 1
SNAPSHOTS_DIR_ENV = "PSEUDOCLANG_SNAPSHOTS_DIR"
MAX_SNAPSHOTS_ENV = "PSEUDOCLANG_MAX_SNAPSHOTS"
DEFAULT_MAX_SNAPSHOTS = 5


def default_snapshots_dir() -> Path:
    """Canonical history location: ``<pseudoclang-repo>/output/snapshots``.

    Fixed (independent of ``--output-dir``) so a bare ``pseudoclang snapshots``
    or ``pseudoclang restore --snapshot N`` in a fresh process finds the history
    without being told where. ``PSEUDOCLANG_SNAPSHOTS_DIR`` overrides it (used to
    relocate or isolate the history, e.g. in tests).
    """
    override = os.environ.get(SNAPSHOTS_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return (default_output_dir() / "snapshots").resolve()


def resolve_max_snapshots(value: int | None) -> int:
    """Resolve the retention count: explicit ``value`` > env > default.

    A negative value is clamped to 0 (0 disables snapshotting entirely).
    """
    if value is None:
        raw = os.environ.get(MAX_SNAPSHOTS_ENV)
        if raw is None or not raw.strip():
            return DEFAULT_MAX_SNAPSHOTS
        try:
            value = int(raw)
        except ValueError:
            return DEFAULT_MAX_SNAPSHOTS
    return max(0, value)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _backup_name(target_key: str) -> str:
    return _sha256(target_key.encode("utf-8")) + ".bak"


@dataclass(frozen=True)
class SnapshotFile:
    """One captured source inside a snapshot."""

    target: Path
    backup: str
    sha256: str


@dataclass(frozen=True)
class SnapshotInfo:
    """A parsed recovery point: its directory, metadata, and captured files."""

    path: Path
    sequence: int
    created_at: str
    label: str
    files: tuple[SnapshotFile, ...]


def _write_manifest_atomically(directory: Path, manifest: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / MANIFEST_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, path)  # atomic swap so a crash never leaves a partial manifest


def _parse_snapshot(directory: Path) -> SnapshotInfo | None:
    """Read one snapshot directory's manifest, or ``None`` if it is not one."""
    try:
        raw = (directory / MANIFEST_NAME).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        return None
    files: list[SnapshotFile] = []
    for entry in data["files"]:
        if not isinstance(entry, dict):
            continue
        target = entry.get("target")
        backup = entry.get("backup")
        sha = entry.get("sha256")
        if not (isinstance(target, str) and isinstance(backup, str) and isinstance(sha, str)):
            continue
        files.append(SnapshotFile(Path(target), backup, sha))
    try:
        sequence = int(data.get("sequence"))
    except (TypeError, ValueError):
        return None
    return SnapshotInfo(
        path=directory,
        sequence=sequence,
        created_at=str(data.get("created_at", "")),
        label=str(data.get("label", "")),
        files=tuple(files),
    )


def _scan(snapshots_dir: Path) -> list[SnapshotInfo]:
    """All valid snapshots under ``snapshots_dir``, sorted oldest-first."""
    try:
        children = [child for child in snapshots_dir.iterdir() if child.is_dir()]
    except OSError:
        return []
    found = [snap for child in children if (snap := _parse_snapshot(child)) is not None]
    found.sort(key=lambda snap: snap.sequence)
    return found


def list_snapshots(snapshots_dir: Path | None = None) -> list[SnapshotInfo]:
    """Return every recovery point, oldest first (newest has the highest seq)."""
    return _scan(snapshots_dir or default_snapshots_dir())


def load_snapshot(
    sequence: int, snapshots_dir: Path | None = None
) -> SnapshotInfo | None:
    """Return the snapshot with ``sequence``, or ``None`` if there is none."""
    for snap in _scan(snapshots_dir or default_snapshots_dir()):
        if snap.sequence == sequence:
            return snap
    return None


def _same_files(snap: SnapshotInfo, files: list[tuple[Path, bytes]]) -> bool:
    """True if ``snap`` already captures exactly these targets and contents."""
    want = {str(path.resolve()): _sha256(data) for path, data in files}
    have = {str(item.target): item.sha256 for item in snap.files}
    return want == have


def _prune(snapshots_dir: Path, max_snapshots: int) -> None:
    """Delete the oldest snapshots until at most ``max_snapshots`` remain."""
    snapshots = _scan(snapshots_dir)
    excess = len(snapshots) - max_snapshots
    for snap in snapshots[:excess] if excess > 0 else []:
        shutil.rmtree(snap.path, ignore_errors=True)


def create_snapshot(
    files: list[tuple[Path, bytes]],
    *,
    label: str,
    max_snapshots: int,
    snapshots_dir: Path | None = None,
    now: datetime | None = None,
) -> SnapshotInfo | None:
    """Capture ``files`` (``(target, original_bytes)``) as a new recovery point.

    Returns the new :class:`SnapshotInfo`, or the existing latest snapshot when
    its contents are identical (no redundant recovery point is written), or
    ``None`` when snapshotting is disabled (``max_snapshots <= 0``) or nothing
    was captured. After writing, the oldest snapshots beyond ``max_snapshots``
    are deleted.

    Best effort: the history must never break the run it protects, so all disk
    errors are swallowed and reported as ``None``.
    """
    if max_snapshots <= 0 or not files:
        return None

    directory = snapshots_dir or default_snapshots_dir()
    try:
        existing = _scan(directory)
        if existing and _same_files(existing[-1], files):
            return existing[-1]  # nothing changed since the last recovery point

        sequence = (existing[-1].sequence + 1) if existing else 1
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        snap_dir = directory / f"{sequence:04d}_{stamp}"
        snap_dir.mkdir(parents=True, exist_ok=True)

        manifest_files = []
        for target, original_bytes in files:
            key = str(target.resolve())
            backup_name = _backup_name(key)
            # Atomic so a crash mid-copy cannot leave a partial recovery point.
            atomic_write_bytes(snap_dir / backup_name, original_bytes)
            manifest_files.append(
                {
                    "target": key,
                    "backup": backup_name,
                    "sha256": _sha256(original_bytes),
                }
            )

        _write_manifest_atomically(
            snap_dir,
            {
                "version": SNAPSHOT_VERSION,
                "sequence": sequence,
                "created_at": (now or datetime.now()).isoformat(timespec="seconds"),
                "label": label,
                "files": manifest_files,
            },
        )
        _prune(directory, max_snapshots)
        return _parse_snapshot(snap_dir)
    except OSError:
        return None


def restore_snapshot(
    sequence: int,
    *,
    dry_run: bool = False,
    snapshots_dir: Path | None = None,
) -> list[RestoreOutcome]:
    """Roll every file captured in snapshot ``sequence`` back to its saved bytes.

    Unlike the crash-recovery restore, this is an explicit, user-named rollback,
    so a target whose on-disk content differs from the snapshot is overwritten
    (that is the point of going back to a recovery point). A target already
    matching the snapshot is reported ``already_clean`` and left untouched.
    ``dry_run`` reports without writing.

    Returns one :class:`RestoreOutcome` per captured file. An empty list means
    no snapshot with that sequence exists.
    """
    directory = snapshots_dir or default_snapshots_dir()
    snap = load_snapshot(sequence, directory)
    if snap is None:
        return []

    outcomes: list[RestoreOutcome] = []
    for item in snap.files:
        backup_file = snap.path / item.backup
        try:
            original_bytes = backup_file.read_bytes()
        except OSError:
            outcomes.append(
                RestoreOutcome(item.target, "missing_backup", f"no backup at {backup_file}")
            )
            continue

        try:
            current = item.target.read_bytes()
        except OSError:
            current = None

        if current is not None and _sha256(current) == item.sha256:
            outcomes.append(RestoreOutcome(item.target, "already_clean"))
            continue

        if dry_run:
            outcomes.append(RestoreOutcome(item.target, "restored"))
            continue

        try:
            item.target.parent.mkdir(parents=True, exist_ok=True)
            # Atomic so a failed rollback write cannot corrupt the target source.
            atomic_write_bytes(item.target, original_bytes)
        except OSError as exc:
            outcomes.append(RestoreOutcome(item.target, "error", str(exc)))
            continue
        outcomes.append(RestoreOutcome(item.target, "restored"))

    return outcomes

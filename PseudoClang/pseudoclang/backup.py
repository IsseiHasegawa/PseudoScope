"""
Persistent source-backup safety net for hard crashes (Phase C).

The in-process restore (a caller's ``finally`` plus the atexit / SIGTERM
back-stop in :mod:`pseudoclang.restore_backstop`) cannot recover from ``SIGKILL``,
an OOM kill, or power loss: the original bytes live only in memory and die with
the process, leaving a mutated file on disk in the target project.

This module adds a last-resort layer that survives a hard crash. Before a source
is mutated, its original bytes are copied to disk under PseudoClang's own
``output/backups`` directory (never inside the target project) and noted in a
manifest. A separate ``pseudoclang restore`` run then reads the manifest and puts
any file we left mutated back to its original bytes. A backup is removed as soon
as its source is restored, so a clean run leaves nothing behind.

The restore is guarded by content hashes so it only ever reverts a file that is
still in the exact mutated state we left it in; a file edited (or deleted) since
is skipped unless ``--force`` is given, so no unrelated change is clobbered.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from pseudoclang.atomicio import atomic_write_bytes
from pseudoclang.validation import default_output_dir

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
BACKUPS_DIR_ENV = "PSEUDOCLANG_BACKUPS_DIR"


def default_backups_dir() -> Path:
    """Canonical backup location: ``<pseudoclang-repo>/output/backups``.

    Fixed (independent of ``--output-dir``) so a bare ``pseudoclang restore`` in
    a fresh process always finds pending backups without being told where. The
    ``PSEUDOCLANG_BACKUPS_DIR`` env var overrides it (used to relocate or isolate
    the safety net, e.g. in tests).
    """
    override = os.environ.get(BACKUPS_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return (default_output_dir() / "backups").resolve()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_path(backups_dir: Path) -> Path:
    return backups_dir / MANIFEST_NAME


def _empty_manifest() -> dict:
    return {"version": MANIFEST_VERSION, "entries": {}}


def _load_manifest(backups_dir: Path) -> dict:
    try:
        raw = _manifest_path(backups_dir).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return _empty_manifest()
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return _empty_manifest()
    return data


def _save_manifest(backups_dir: Path, manifest: dict) -> None:
    backups_dir.mkdir(parents=True, exist_ok=True)
    path = _manifest_path(backups_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, path)  # atomic swap so a crash never leaves a partial manifest


def _backup_name(target_key: str) -> str:
    return _sha256(target_key.encode("utf-8")) + ".bak"


def record(
    target: Path,
    *,
    original_bytes: bytes,
    mutated_bytes: bytes,
    function: str | None = None,
    backups_dir: Path | None = None,
) -> None:
    """Persist ``target``'s original bytes and note the mutant now on disk.

    The original backup is written once per file; the manifest's mutated hash is
    refreshed on every call so it always matches what is currently on disk (the
    restore guard relies on this). Best effort: the safety net must never break
    the run it protects, so all errors are swallowed.
    """
    directory = backups_dir or default_backups_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        key = str(target.resolve())
        backup_name = _backup_name(key)
        backup_file = directory / backup_name
        if not backup_file.exists():
            # Atomic so a crash mid-copy cannot leave a partial backup that would
            # later restore truncated bytes over the user's source.
            atomic_write_bytes(backup_file, original_bytes)
        manifest = _load_manifest(directory)
        manifest["entries"][key] = {
            "backup": backup_name,
            "original_sha256": _sha256(original_bytes),
            "mutated_sha256": _sha256(mutated_bytes),
            "function": function,
        }
        _save_manifest(directory, manifest)
    except OSError:
        pass


def clear(target: Path, *, backups_dir: Path | None = None) -> None:
    """Drop ``target``'s backup file and manifest entry after a good restore."""
    directory = backups_dir or default_backups_dir()
    try:
        key = str(target.resolve())
        manifest = _load_manifest(directory)
        entry = manifest["entries"].pop(key, None)
        if entry is None:
            return
        _unlink_quietly(directory / entry.get("backup", ""))
        _save_manifest(directory, manifest)
    except OSError:
        pass


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


@dataclass
class RestoreOutcome:
    """Result of trying to restore one backed-up source."""

    target: Path
    status: str  # see _restore_one for the vocabulary
    detail: str = ""

    @property
    def resolved(self) -> bool:
        """True when the target no longer needs our attention."""
        return self.status in ("restored", "forced", "already_clean")


def _is_within(target: Path, root: Path) -> bool:
    try:
        Path(os.path.abspath(target)).relative_to(root)
        return True
    except ValueError:
        return False


def _restore_one(
    backups_dir: Path,
    target: Path,
    entry: dict,
    *,
    dry_run: bool,
    force: bool,
) -> RestoreOutcome:
    """Decide and (unless dry-run) perform the restore for one file.

    Statuses: ``restored`` (was our mutant, reverted), ``forced`` (content
    differed but --force reverted it), ``already_clean`` (already original),
    ``skipped_user_edit`` (edited since; left alone), ``skipped_missing`` (file
    gone), ``missing_backup`` / ``error`` (could not restore).
    """
    backup_file = backups_dir / entry.get("backup", "")
    try:
        original_bytes = backup_file.read_bytes()
    except OSError:
        return RestoreOutcome(target, "missing_backup", f"no backup at {backup_file}")

    original_sha = entry.get("original_sha256")
    mutated_sha = entry.get("mutated_sha256")

    try:
        current = target.read_bytes()
    except OSError:
        current = None

    if current is not None and _sha256(current) == original_sha:
        return RestoreOutcome(target, "already_clean")

    is_our_mutant = current is not None and _sha256(current) == mutated_sha
    if not (is_our_mutant or force):
        if current is None:
            return RestoreOutcome(target, "skipped_missing", "target file is missing")
        return RestoreOutcome(
            target,
            "skipped_user_edit",
            "on-disk content matches neither the original nor our mutant",
        )

    if dry_run:
        return RestoreOutcome(target, "restored" if is_our_mutant else "forced")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic so a failed crash-restore write cannot corrupt the source.
        atomic_write_bytes(target, original_bytes)
    except OSError as exc:
        return RestoreOutcome(target, "error", str(exc))
    return RestoreOutcome(target, "restored" if is_our_mutant else "forced")


def restore_pending(
    *,
    project_root: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    backups_dir: Path | None = None,
) -> list[RestoreOutcome]:
    """Restore every source a crashed run left mutated (optionally scoped).

    Only files still in their recorded mutated state are reverted; edited or
    deleted files are skipped unless ``force``. Resolved entries are dropped from
    the manifest (and their backups deleted). ``dry_run`` reports without writing
    and without clearing anything.
    """
    directory = backups_dir or default_backups_dir()
    manifest = _load_manifest(directory)
    entries = manifest["entries"]
    root = project_root.resolve() if project_root is not None else None

    outcomes: list[RestoreOutcome] = []
    changed = False
    for key, entry in list(entries.items()):
        target = Path(key)
        if root is not None and not _is_within(target, root):
            continue
        outcome = _restore_one(
            directory, target, entry, dry_run=dry_run, force=force
        )
        outcomes.append(outcome)
        if not dry_run and outcome.resolved:
            _unlink_quietly(directory / entry.get("backup", ""))
            entries.pop(key, None)
            changed = True

    if changed:
        _save_manifest(directory, manifest)
    return outcomes

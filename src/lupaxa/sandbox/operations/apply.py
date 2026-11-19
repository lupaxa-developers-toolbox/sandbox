"""Apply workspace changes back to the original project."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from lupaxa.sandbox.exceptions import StagingError, UsageError
from lupaxa.sandbox.operations.export import _copy
from lupaxa.sandbox.operations.paths import safe_join
from lupaxa.sandbox.sessions.store import Session, SessionStore, new_id
from lupaxa.sandbox.workspace.changes import Change, compare_manifests
from lupaxa.sandbox.workspace.manifest import Entry, Manifest, build_manifest
from lupaxa.sandbox.workspace.patterns import DEFAULT_TRACKING_EXCLUDES, is_included

_WRITES = {"create", "replace", "delete", "chmod"}


@dataclass(frozen=True)
class ApplyItem:
    """One planned or completed change to the original project."""

    path: str
    action: str
    reason: str
    pre_hash: str | None
    post_hash: str | None


@dataclass
class ApplyResult:
    """What apply did, plus the recovery journal when files were written.

    A multi-file apply is not atomic. The journal lists files already written
    and keeps backups of the originals.
    """

    items: list[ApplyItem]
    dry_run: bool
    journal: Path | None
    partial: bool
    error: str

    @property
    def conflicts(self) -> list[ApplyItem]:
        return [item for item in self.items if item.action == "conflict"]


def apply_changes(
    *,
    name: str,
    data_dir: Path,
    include: list[str] | None = None,
    select_all: bool = False,
    allow_delete: bool = False,
    allow_links: bool = False,
    dry_run: bool = False,
) -> ApplyResult:
    """Apply selected workspace changes that still match the source baseline."""
    patterns = list(include or [])
    if not select_all and not patterns:
        raise UsageError("Pass --all or at least one --include pattern")
    store = SessionStore(data_dir)
    session = store.get(name)
    source_root = Path(session.source)
    if not source_root.is_dir():
        raise StagingError(f"Source is not a directory: {source_root}")
    baseline = store.baseline(session)
    source_baseline = store.source_baseline(session)
    current = build_manifest(session.workspace, exclude=[])
    changes = compare_manifests(baseline, current, DEFAULT_TRACKING_EXCLUDES)
    items = [
        _plan(
            session,
            source_root,
            source_baseline,
            current,
            change,
            allow_delete=allow_delete,
            allow_links=allow_links,
        )
        for change in changes.changes
        if _selected(change, baseline, current, patterns)
    ]
    writable = [item for item in items if item.action in _WRITES]
    if dry_run or not writable:
        outcome = "dry-run" if dry_run else _outcome(items, partial=False)
        _record(store, session, dry_run, outcome, items)
        return ApplyResult(items, dry_run, None, False, "")
    journal, partial, error = _commit(session, source_root, current, writable)
    outcome = "partial" if partial else _outcome(items, partial=False)
    _record(store, session, False, outcome, items)
    return ApplyResult(items, False, journal, partial, error)


def _selected(change: Change, baseline: Manifest, current: Manifest, patterns: list[str]) -> bool:
    if not patterns:
        return True
    entry = current.entries.get(change.path) or baseline.entries.get(change.path)
    is_dir = entry is not None and entry.type == "directory"
    return is_included(patterns, change.path, is_dir=is_dir)


def _plan(
    session: Session,
    source_root: Path,
    source_baseline: Manifest,
    current: Manifest,
    change: Change,
    *,
    allow_delete: bool,
    allow_links: bool,
) -> ApplyItem:
    try:
        destination = safe_join(source_root, change.path)
    except UsageError as exc:
        return ApplyItem(change.path, "conflict", str(exc), None, None)
    original = source_baseline.entries.get(change.path)
    workspace = current.entries.get(change.path)
    post_hash = workspace.sha256 if workspace is not None else None
    if change.category == "deleted":
        return _plan_delete(change.path, destination, original, allow_delete)
    if change.category in {"link_changed", "type_changed"}:
        if not allow_links:
            return ApplyItem(
                change.path,
                "skip",
                "link replacement needs --replace-links",
                None,
                None,
            )
        if not _source_matches(destination, original):
            return _conflict(change.path, destination)
        return ApplyItem(change.path, "replace", "", _file_hash(destination), post_hash)
    if change.category == "permissions_changed":
        if not _source_matches(destination, original):
            return _conflict(change.path, destination)
        pre_hash = original.sha256 if original is not None else None
        return ApplyItem(change.path, "chmod", "", pre_hash, pre_hash)
    if change.category == "added" and not destination.exists() and not destination.is_symlink():
        return ApplyItem(change.path, "create", "", None, post_hash)
    if _same_bytes(destination, session.workspace / change.path):
        digest = _file_hash(destination)
        return ApplyItem(change.path, "skip", "already matches", digest, digest)
    if not _source_matches(destination, original):
        return _conflict(change.path, destination)
    pre_hash = original.sha256 if original is not None else _file_hash(destination)
    return ApplyItem(change.path, "replace", "", pre_hash, post_hash)


def _plan_delete(
    path: str,
    destination: Path,
    original: Entry | None,
    allow_delete: bool,
) -> ApplyItem:
    if not allow_delete:
        return ApplyItem(path, "skip", "delete needs --delete", None, None)
    if not destination.exists() and not destination.is_symlink():
        return ApplyItem(path, "skip", "already absent", None, None)
    if not _source_matches(destination, original):
        return _conflict(path, destination)
    return ApplyItem(path, "delete", "", _file_hash(destination), None)


def _conflict(path: str, destination: Path) -> ApplyItem:
    return ApplyItem(
        path,
        "conflict",
        "source changed since the session was created",
        _file_hash(destination),
        None,
    )


def _source_matches(path: Path, entry: Entry | None) -> bool:
    if entry is None:
        return not path.exists() and not path.is_symlink()
    if entry.type == "symlink":
        return path.is_symlink() and os.readlink(path) == entry.target
    if entry.type == "directory":
        return path.is_dir() and not path.is_symlink()
    if entry.type != "file":
        return False
    if not path.is_file() or path.is_symlink():
        return False
    if _file_hash(path) != entry.sha256:
        return False
    return entry.mode is None or _mode(path) == entry.mode


def _same_bytes(source: Path, workspace: Path) -> bool:
    if source.is_symlink() or workspace.is_symlink():
        if not source.is_symlink() or not workspace.is_symlink():
            return False
        return os.readlink(source) == os.readlink(workspace)
    if not source.is_file() or not workspace.is_file():
        return False
    return _file_hash(source) == _file_hash(workspace)


def _commit(
    session: Session,
    source_root: Path,
    current: Manifest,
    items: list[ApplyItem],
) -> tuple[Path, bool, str]:
    journal_dir = session.root / "applies" / new_id()
    backup_root = journal_dir / "backup"
    journal_dir.mkdir(parents=True)
    journal: dict[str, object] = {
        "atomic": False,
        "status": "started",
        "note": (
            "A multi-file apply is not atomic. Files already written are listed in "
            "completed. Originals are in backup/."
        ),
        "completed": [],
    }
    _write_journal(journal_dir, journal)
    ordered = sorted(items, key=lambda item: (item.action != "delete", -item.path.count("/")))
    for item in ordered:
        try:
            _apply_one(session, source_root, current, backup_root, item)
        except OSError as exc:
            journal["status"] = "partial"
            journal["error"] = str(exc)
            _write_journal(journal_dir, journal)
            return journal_dir, True, str(exc)
        completed = journal["completed"]
        if isinstance(completed, list):
            completed.append(item.path)
        _write_journal(journal_dir, journal)
    journal["status"] = "completed"
    _write_journal(journal_dir, journal)
    return journal_dir, False, ""


def _apply_one(
    session: Session,
    source_root: Path,
    current: Manifest,
    backup_root: Path,
    item: ApplyItem,
) -> None:
    destination = safe_join(source_root, item.path)
    workspace_path = session.workspace / item.path
    if destination.exists() or destination.is_symlink():
        _backup(destination, backup_root / item.path)
    if item.action == "delete":
        if destination.is_dir() and not destination.is_symlink():
            destination.rmdir()
        else:
            destination.unlink()
        return
    if item.action == "chmod":
        entry = current.entries[item.path]
        if entry.mode is None:
            raise OSError(f"No mode recorded for {item.path}")
        os.chmod(destination, int(entry.mode, 8))
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    _copy(workspace_path, destination)


def _backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        os.symlink(os.readlink(source), destination)
        return
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        return
    shutil.copy2(source, destination, follow_symlinks=False)


def _record(
    store: SessionStore,
    session: Session,
    dry_run: bool,
    outcome: str,
    items: list[ApplyItem],
) -> None:
    store.record_apply(
        session,
        dry_run=dry_run,
        outcome=outcome,
        selected=[
            item.path for item in items if item.action in _WRITES or item.action == "conflict"
        ],
        conflicts=[item.path for item in items if item.action == "conflict"],
        detail=[
            {
                "path": item.path,
                "action": item.action,
                "reason": item.reason,
                "pre_hash": item.pre_hash or "",
                "post_hash": item.post_hash or "",
            }
            for item in items
        ],
    )


def _outcome(items: list[ApplyItem], *, partial: bool) -> str:
    if partial:
        return "partial"
    if any(item.action == "conflict" for item in items):
        return "conflicts"
    if any(item.action in _WRITES for item in items):
        return "applied"
    return "unchanged"


def _write_journal(directory: Path, journal: dict[str, object]) -> None:
    temporary = directory / "journal.json.tmp"
    temporary.write_text(json.dumps(journal, indent=2) + "\n")
    temporary.replace(directory / "journal.json")


def _file_hash(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _mode(path: Path) -> str:
    import stat

    return f"{stat.S_IMODE(path.stat().st_mode):04o}"

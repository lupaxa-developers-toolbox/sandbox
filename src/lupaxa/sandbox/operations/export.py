"""Copy selected workspace files out without deleting them from the session."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from lupaxa.sandbox.exceptions import UsageError
from lupaxa.sandbox.operations.paths import link_escapes, safe_join
from lupaxa.sandbox.sessions.store import SessionStore
from lupaxa.sandbox.workspace.manifest import build_manifest
from lupaxa.sandbox.workspace.patterns import is_included

_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class ExportItem:
    """One file in an export plan."""

    path: str
    action: str
    reason: str = ""


@dataclass
class ExportResult:
    """The plan, and whether the destination was left untouched."""

    items: list[ExportItem]
    dry_run: bool
    destination: Path


def export_files(
    *,
    name: str,
    data_dir: Path,
    include: list[str],
    destination: Path,
    dry_run: bool = False,
    yes: bool = False,
    force: bool = False,
) -> ExportResult:
    """Copy included workspace files into ``destination``.

    Existing files are skipped unless ``yes`` is set. Newer destination files
    stay in place unless ``force`` is set. Links that point outside the
    workspace are rejected before anything is copied.
    """
    if not include:
        raise UsageError("Pass at least one --include pattern")
    destination = Path(destination)
    if destination.exists() and not destination.is_dir():
        raise UsageError(f"Export destination is not a directory: {destination}")
    store = SessionStore(data_dir)
    session = store.get(name)
    chosen = _select(session.workspace, include)
    items: list[ExportItem] = []
    for relative in chosen:
        source = session.workspace / relative
        if link_escapes(source, session.workspace):
            items.append(ExportItem(relative, "reject-link", "link points outside the workspace"))
            continue
        try:
            target = safe_join(destination, relative)
        except UsageError as exc:
            items.append(ExportItem(relative, "reject-escape", str(exc)))
            continue
        items.append(_decide(relative, source, target, yes=yes or force, force=force))
    rejected = [item for item in items if item.action.startswith("reject")]
    detail = [_detail(item) for item in items]
    if rejected:
        store.record_export(
            session,
            destination=str(destination),
            include=include,
            dry_run=dry_run,
            outcome="rejected",
            detail=detail,
        )
        lines = "\n".join(f"{item.path}: {item.reason}" for item in rejected)
        raise UsageError(f"Export rejected:\n{lines}")
    if not dry_run:
        destination.mkdir(parents=True, exist_ok=True)
        for item in items:
            if item.action in {"create", "replace"}:
                _copy(session.workspace / item.path, safe_join(destination, item.path))
    store.record_export(
        session,
        destination=str(destination),
        include=include,
        dry_run=dry_run,
        outcome="dry-run" if dry_run else "copied",
        detail=detail,
    )
    return ExportResult(items=items, dry_run=dry_run, destination=destination)


def _select(workspace: Path, patterns: list[str]) -> list[str]:
    manifest = build_manifest(workspace, exclude=[])
    chosen = [
        path
        for path, entry in manifest.entries.items()
        if entry.type != "directory" and is_included(patterns, path, is_dir=False)
    ]
    if not chosen:
        raise UsageError("No workspace files matched the include patterns")
    return sorted(chosen)


def _decide(relative: str, source: Path, target: Path, *, yes: bool, force: bool) -> ExportItem:
    if not target.exists() and not target.is_symlink():
        return ExportItem(relative, "create")
    if not force and _newer(target, source):
        return ExportItem(relative, "skip-newer", "destination is newer")
    if yes:
        return ExportItem(relative, "replace")
    return ExportItem(relative, "skip-exists", "destination exists")


def _newer(destination: Path, source: Path) -> bool:
    try:
        return destination.lstat().st_mtime_ns > source.lstat().st_mtime_ns
    except OSError:
        return False


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".sandbox-tmp")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    try:
        if source.is_symlink():
            os.symlink(os.readlink(source), temporary)
        else:
            with source.open("rb") as incoming, temporary.open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=_CHUNK)
            shutil.copystat(source, temporary, follow_symlinks=False)
        os.replace(temporary, target)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def _detail(item: ExportItem) -> dict[str, str]:
    return {"path": item.path, "action": item.action, "reason": item.reason}

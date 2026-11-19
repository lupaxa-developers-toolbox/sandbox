"""Remove old sessions and report how much disk they use."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lupaxa.sandbox.exceptions import UsageError
from lupaxa.sandbox.sessions.store import Session, SessionStore, _pid_alive

_DEFAULT_AGE = timedelta(days=14)
_INCOMPLETE_AGE = timedelta(hours=1)


@dataclass(frozen=True)
class CleanItem:
    """A session clean would remove, or did remove."""

    name: str
    reason: str


@dataclass
class CleanResult:
    """Sessions removed or left because they are still running."""

    removed: list[CleanItem]
    skipped_running: list[str]
    dry_run: bool


@dataclass(frozen=True)
class StorageEntry:
    """Disk use of one session directory."""

    name: str
    status: str
    size_bytes: int


@dataclass
class StorageReport:
    """Total workspace use and the largest sessions."""

    session_count: int
    total_bytes: int
    largest: list[StorageEntry]


def clean_sessions(
    *,
    data_dir: Path,
    older_than: str | None = None,
    failed: bool = False,
    dry_run: bool = False,
    incomplete_after: timedelta = _INCOMPLETE_AGE,
) -> CleanResult:
    """Remove eligible sessions. A running session is never removed."""
    if older_than is not None:
        age_limit = parse_age(older_than)
    elif failed:
        age_limit = None
    else:
        age_limit = _DEFAULT_AGE
    store = SessionStore(data_dir)
    now = datetime.now(UTC)
    removed: list[CleanItem] = []
    skipped: list[str] = []
    for session in store.list_sessions():
        if _running(session):
            skipped.append(session.name)
            continue
        reason = _reason(session, now, age_limit, failed, incomplete_after)
        if reason is None:
            continue
        removed.append(CleanItem(session.name, reason))
        if not dry_run:
            store.destroy(session.name)
    return CleanResult(removed, skipped, dry_run)


def storage_report(data_dir: Path) -> StorageReport:
    """Sum session directories without following links out of them."""
    store = SessionStore(data_dir)
    entries = [
        StorageEntry(session.name, session.status, directory_size(session.root))
        for session in store.list_sessions()
    ]
    entries.sort(key=lambda item: item.size_bytes, reverse=True)
    return StorageReport(
        session_count=len(entries),
        total_bytes=sum(item.size_bytes for item in entries),
        largest=entries[:5],
    )


def parse_age(text: str) -> timedelta:
    """Parse ``14d``, ``12h``, or ``30m``."""
    match = re.fullmatch(r"(\d+)([dhm])", text.strip())
    if match is None:
        raise UsageError("Age must look like 14d, 12h, or 30m")
    count = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        return timedelta(days=count)
    if unit == "h":
        return timedelta(hours=count)
    return timedelta(minutes=count)


def directory_size(root: Path) -> int:
    """Byte size of files and links stored under ``root``."""
    if not root.exists():
        return 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            path = current / name
            if path.is_symlink():
                total += path.lstat().st_size
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = current / name
            if path.is_symlink() or path.is_file():
                total += path.lstat().st_size
    return total


def _running(session: Session) -> bool:
    return session.status == "running" and session.pid is not None and _pid_alive(session.pid)


def _reason(
    session: Session,
    now: datetime,
    age_limit: timedelta | None,
    failed: bool,
    incomplete_after: timedelta,
) -> str | None:
    created = datetime.fromisoformat(session.created_at)
    age = now - created
    incomplete = session.status == "created" and not (session.root / "baseline.json").is_file()
    if incomplete and age >= incomplete_after:
        return "incomplete"
    if age_limit is not None and age >= age_limit:
        return "old"
    if failed and session.status == "failed":
        return "failed"
    return None

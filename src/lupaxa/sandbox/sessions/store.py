"""Session metadata, logs, and the one-command-at-a-time lock.

SQLite holds session identity, status, and export/apply history. Manifests,
workspaces, and command logs stay in the session directory.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lupaxa.sandbox.exceptions import NotFoundError, SessionBusy, UsageError
from lupaxa.sandbox.sessions.database import Database
from lupaxa.sandbox.workspace.changes import Change, ChangeSet
from lupaxa.sandbox.workspace.manifest import Manifest

_RUN_GUARD = threading.Lock()
_RUN_LOCKS: dict[str, threading.Lock] = {}
_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


@dataclass
class ExecutionRecord:
    """One command recorded against a session."""

    id: str
    command: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    termination_reason: str
    duration_seconds: float
    started_at: str
    ended_at: str
    cwd: str


@dataclass
class Session:
    """A retained workspace and the commands that have run in it."""

    id: str
    name: str
    status: str
    source: str
    workspace: Path
    root: Path
    executions: list[ExecutionRecord]
    outside_links: list[str]
    pid: int | None
    created_at: str


class SessionStore:
    """Create, load, and destroy sessions under a data directory."""

    def __init__(self, data: Path) -> None:
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.data / "sessions.db")
        self._import_legacy()

    def create(self, name: str, source: Path) -> Session:
        _check_name(name)
        created_at = _now()
        session_id = uuid.uuid4().hex
        with self.db.transaction() as conn:
            existing = conn.execute("SELECT 1 FROM sessions WHERE name = ?", (name,)).fetchone()
            if existing is not None:
                raise UsageError(f"Session already exists: {name}")
            conn.execute(
                """
                INSERT INTO sessions (id, name, status, source, created_at, pid, outside_links)
                VALUES (?, ?, 'created', ?, ?, NULL, '[]')
                """,
                (session_id, name, str(source), created_at),
            )
        root = self.data / "sessions" / session_id
        root.mkdir(parents=True)
        _write_metadata(root, session_id, name, "created", str(source), created_at, None, [])
        return self.get(name)

    def get(self, name: str) -> Session:
        rows = self.db.query(
            "SELECT * FROM sessions WHERE name = ? OR id = ?",
            (name, name),
        )
        if not rows:
            raise NotFoundError(f"Session not found: {name}")
        row = rows[0]
        session_id = str(row["id"])
        root = self.data / "sessions" / session_id
        pid = row["pid"] if isinstance(row["pid"], int) else None
        status = str(row["status"])
        if status == "running" and pid is not None and not _pid_alive(pid):
            status = "interrupted"
            pid = None
            self._update_row(session_id, status, pid)
        links = _decode_links(row["outside_links"])
        return Session(
            id=session_id,
            name=str(row["name"]),
            status=status,
            source=str(row["source"]),
            workspace=root / "workspace" / "project",
            root=root,
            executions=_load_executions(root),
            outside_links=links,
            pid=pid,
            created_at=str(row["created_at"]),
        )

    def list_sessions(self) -> list[Session]:
        rows = self.db.query("SELECT name FROM sessions ORDER BY name")
        return [self.get(str(row["name"])) for row in rows]

    def save_baseline(
        self,
        session: Session,
        manifest: Manifest,
        outside: list[str],
        source_manifest: Manifest | None = None,
    ) -> None:
        source_snapshot = source_manifest if source_manifest is not None else manifest
        _write_json(session.root / "baseline.json", manifest.to_dict())
        _write_json(session.root / "source-baseline.json", source_snapshot.to_dict())
        self._update_row(session.id, "ready", None, outside)
        _write_metadata(
            session.root,
            session.id,
            session.name,
            "ready",
            session.source,
            session.created_at,
            None,
            outside,
        )
        self._record_snapshot(session.id, "baseline", "baseline.json")
        self._record_snapshot(session.id, "source-baseline", "source-baseline.json")

    def baseline(self, session: Session) -> Manifest:
        return _read_manifest(session.root / "baseline.json", "Baseline manifest is missing")

    def source_baseline(self, session: Session) -> Manifest:
        return _read_manifest(session.root / "source-baseline.json", "Source baseline is missing")

    def set_status(self, session: Session, status: str, pid: int | None) -> None:
        if not session.root.is_dir():
            return
        self._update_row(session.id, status, pid)
        _write_metadata(
            session.root,
            session.id,
            session.name,
            status,
            session.source,
            session.created_at,
            pid,
            session.outside_links,
        )

    def set_created_at(self, name: str, when: datetime) -> None:
        session = self.get(name)
        stamp = when.astimezone(UTC).isoformat()
        with self.db.transaction() as conn:
            conn.execute("UPDATE sessions SET created_at = ? WHERE id = ?", (stamp, session.id))

    def add_execution(
        self,
        session: Session,
        record: ExecutionRecord,
        changes: ChangeSet,
        snapshot: Manifest,
    ) -> None:
        if not session.root.is_dir():
            return
        folder = session.root / "executions" / record.id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "stdout.log").write_text(record.stdout)
        (folder / "stderr.log").write_text(record.stderr)
        _write_json(
            folder / "result.json",
            {
                "id": record.id,
                "command": record.command,
                "exit_code": record.exit_code,
                "termination_reason": record.termination_reason,
                "duration_seconds": record.duration_seconds,
                "started_at": record.started_at,
                "ended_at": record.ended_at,
                "cwd": record.cwd,
            },
        )
        _write_json(
            folder / "changes.json",
            {
                "incomplete": changes.incomplete,
                "errors": changes.errors,
                "changes": [
                    {"path": change.path, "category": change.category} for change in changes.changes
                ],
            },
        )
        _write_json(session.root / "snapshot.json", snapshot.to_dict())
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO executions (
                    id, session_id, command, exit_code, termination_reason,
                    duration_seconds, started_at, ended_at, cwd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    session.id,
                    json.dumps(record.command),
                    record.exit_code,
                    record.termination_reason,
                    record.duration_seconds,
                    record.started_at,
                    record.ended_at,
                    record.cwd,
                ),
            )
            conn.execute(
                """
                INSERT INTO changesets (id, session_id, execution_id, path, incomplete, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    session.id,
                    record.id,
                    f"executions/{record.id}/changes.json",
                    int(changes.incomplete),
                    record.ended_at,
                ),
            )
        self._record_snapshot(session.id, "latest", "snapshot.json")

    @contextlib.contextmanager
    def acquire_run(self, session: Session) -> Iterator[None]:
        """Hold the session so a second mutating command fails immediately."""
        thread_lock = _thread_lock(session.id)
        if not thread_lock.acquire(blocking=False):
            raise SessionBusy(f"Session {session.name} is already running a command")
        handle = (session.root / "run.lock").open("a+")
        locked = False
        try:
            if os.name == "posix":
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except BlockingIOError as exc:
                    message = f"Session {session.name} is already running a command"
                    raise SessionBusy(message) from exc
            yield
        finally:
            if locked:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            thread_lock.release()

    def destroy(self, name: str, *, force: bool = False) -> None:
        session = self.get(name)
        alive = session.pid is not None and _pid_alive(session.pid)
        if session.status == "running" and alive and not force:
            raise SessionBusy(f"Session {name} has a running command. Pass --force to stop it.")
        if alive and force and session.pid is not None:
            _kill_group(session.pid)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM executions WHERE session_id = ?", (session.id,))
            conn.execute("DELETE FROM snapshots WHERE session_id = ?", (session.id,))
            conn.execute("DELETE FROM changesets WHERE session_id = ?", (session.id,))
            conn.execute("DELETE FROM exports WHERE session_id = ?", (session.id,))
            conn.execute("DELETE FROM apply_operations WHERE session_id = ?", (session.id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session.id,))
        shutil.rmtree(session.root, ignore_errors=True)

    def record_export(
        self,
        session: Session,
        *,
        destination: str,
        include: list[str],
        dry_run: bool,
        outcome: str,
        detail: list[dict[str, str]],
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO exports (
                    id, session_id, destination, include, dry_run, outcome, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    session.id,
                    destination,
                    json.dumps(include),
                    int(dry_run),
                    outcome,
                    json.dumps(detail),
                    _now(),
                ),
            )

    def record_apply(
        self,
        session: Session,
        *,
        dry_run: bool,
        outcome: str,
        selected: list[str],
        conflicts: list[str],
        detail: list[dict[str, str]],
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO apply_operations (
                    id, session_id, dry_run, outcome, selected, conflicts, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    session.id,
                    int(dry_run),
                    outcome,
                    json.dumps(selected),
                    json.dumps(conflicts),
                    json.dumps(detail),
                    _now(),
                ),
            )

    def _update_row(
        self,
        session_id: str,
        status: str,
        pid: int | None,
        outside: list[str] | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            if outside is None:
                conn.execute(
                    "UPDATE sessions SET status = ?, pid = ? WHERE id = ?",
                    (status, pid, session_id),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET status = ?, pid = ?, outside_links = ? WHERE id = ?",
                    (status, pid, json.dumps(outside), session_id),
                )

    def _record_snapshot(self, session_id: str, kind: str, relative: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO snapshots (id, session_id, kind, path, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (new_id(), session_id, kind, relative, _now()),
            )

    def _import_legacy(self) -> None:
        if self.db.query("SELECT 1 FROM sessions LIMIT 1"):
            return
        names = self._read_names()
        if not names:
            return
        with self.db.transaction() as conn:
            for name, session_id in names.items():
                meta_path = self.data / "sessions" / session_id / "metadata.json"
                meta = _read_json(meta_path) if meta_path.is_file() else {}
                links = meta.get("outside_links", [])
                conn.execute(
                    """
                    INSERT OR IGNORE INTO sessions (
                        id, name, status, source, created_at, pid, outside_links
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        name,
                        str(meta.get("status", "ready")),
                        str(meta.get("source", "")),
                        str(meta.get("created_at", _now())),
                        meta.get("pid") if isinstance(meta.get("pid"), int) else None,
                        json.dumps(links if isinstance(links, list) else []),
                    ),
                )

    def _read_names(self) -> dict[str, str]:
        path = self.data / "names.json"
        if not path.is_file():
            return {}
        payload = _read_json(path)
        return {str(key): str(value) for key, value in payload.items()}


def _write_metadata(
    root: Path,
    session_id: str,
    name: str,
    status: str,
    source: str,
    created_at: str,
    pid: int | None,
    outside: list[str],
) -> None:
    _write_json(
        root / "metadata.json",
        {
            "id": session_id,
            "name": name,
            "status": status,
            "source": source,
            "pid": pid,
            "outside_links": outside,
            "created_at": created_at,
        },
    )


def _decode_links(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload]


def _read_manifest(path: Path, missing: str) -> Manifest:
    if not path.is_file():
        return Manifest({}, True, [missing])
    return Manifest.from_dict(_read_json(path))


def _read_log(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text()


def _load_executions(root: Path) -> list[ExecutionRecord]:
    folder = root / "executions"
    if not folder.is_dir():
        return []
    records: list[ExecutionRecord] = []
    for child in folder.iterdir():
        result = child / "result.json"
        if not result.is_file():
            continue
        payload = _read_json(result)
        command = payload.get("command", [])
        exit_code = payload.get("exit_code")
        records.append(
            ExecutionRecord(
                id=str(payload.get("id", child.name)),
                command=[str(part) for part in command] if isinstance(command, list) else [],
                exit_code=exit_code if isinstance(exit_code, int) else None,
                stdout=_read_log(child / "stdout.log"),
                stderr=_read_log(child / "stderr.log"),
                termination_reason=str(payload.get("termination_reason", "completed")),
                duration_seconds=_as_float(payload.get("duration_seconds")),
                started_at=str(payload.get("started_at", "")),
                ended_at=str(payload.get("ended_at", "")),
                cwd=str(payload.get("cwd", "")),
            )
        )
    return sorted(records, key=lambda item: item.started_at)


def _check_name(name: str) -> None:
    import re

    if re.fullmatch(_NAME, name) is None:
        raise UsageError(
            "Session names start with a letter or digit and contain only letters, digits, dots, "
            "underscores, and hyphens"
        )


def _thread_lock(session_id: str) -> threading.Lock:
    with _RUN_GUARD:
        return _RUN_LOCKS.setdefault(session_id, threading.Lock())


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_group(pid: int) -> None:
    if os.name != "posix":
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(20):
        if not _pid_alive(pid):
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        return {}
    return payload


def _as_float(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def new_id() -> str:
    return uuid.uuid4().hex


def load_saved_changes(root: Path, execution_id: str) -> ChangeSet:
    path = root / "executions" / execution_id / "changes.json"
    if not path.is_file():
        return ChangeSet([], True, ["Change set is missing"])
    payload = _read_json(path)
    raw = payload.get("changes", [])
    changes: list[Change] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                changes.append(Change(str(item.get("category", "")), str(item.get("path", ""))))
    errors = payload.get("errors", [])
    return ChangeSet(
        changes=changes,
        incomplete=bool(payload.get("incomplete")),
        errors=[str(item) for item in errors] if isinstance(errors, list) else [],
    )

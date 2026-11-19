"""SQLite index for session metadata.

Manifests, workspaces, and command logs stay on the filesystem. The database
stores identity, status, and the history of exports and applies.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    pid INTEGER,
    outside_links TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    command TEXT NOT NULL,
    exit_code INTEGER,
    termination_reason TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    cwd TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changesets (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    execution_id TEXT,
    path TEXT NOT NULL,
    incomplete INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    destination TEXT NOT NULL,
    include TEXT NOT NULL,
    dry_run INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS apply_operations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    dry_run INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    selected TEXT NOT NULL,
    conflicts TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comparisons (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
"""


class Database:
    """One connection with a lock so sessions can be updated from a worker thread."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close the connection. Repeated calls do nothing."""
        conn = getattr(self, "_conn", None)
        self._conn = None
        if conn is not None:
            conn.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise sqlite3.ProgrammingError("The session database is closed.")
        return self._conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connection()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def query(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection().execute(sql, params))

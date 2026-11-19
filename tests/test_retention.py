"""Cleanup leaves running sessions alone and storage reports disk use."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lupaxa.sandbox.operations.retention import clean_sessions, storage_report
from lupaxa.sandbox.service import create_session
from lupaxa.sandbox.sessions.store import SessionStore


def test_default_clean_removes_old_sessions_only(tmp_path: Path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "note.txt").write_text("v1\n")
    data = tmp_path / "data"
    create_session(source=source, name="old", data_dir=data)
    create_session(source=source, name="fresh", data_dir=data)
    store = SessionStore(data)
    store.set_created_at("old", datetime.now(UTC) - timedelta(days=30))
    preview = clean_sessions(data_dir=data, dry_run=True)
    assert [item.name for item in preview.removed] == ["old"]
    assert store.get("old").root.is_dir()
    clean_sessions(data_dir=data)
    missing = list(store.db.query("SELECT name FROM sessions WHERE name = 'old'"))
    assert missing == []
    assert store.get("fresh").name == "fresh"


def test_clean_skips_a_live_process(tmp_path: Path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    data = tmp_path / "data"
    create_session(source=source, name="busy", data_dir=data)
    store = SessionStore(data)
    store.set_created_at("busy", datetime.now(UTC) - timedelta(days=30))
    store.set_status(store.get("busy"), "running", os.getpid())
    result = clean_sessions(data_dir=data)
    assert result.removed == []
    assert result.skipped_running == ["busy"]
    assert store.get("busy").status == "running"


def test_failed_filter_removes_only_failed_sessions(tmp_path: Path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    data = tmp_path / "data"
    create_session(source=source, name="bad", data_dir=data)
    create_session(source=source, name="good", data_dir=data)
    store = SessionStore(data)
    store.set_status(store.get("bad"), "failed", None)
    clean_sessions(data_dir=data, failed=True)
    names = {str(row["name"]) for row in store.db.query("SELECT name FROM sessions")}
    assert names == {"good"}


def test_storage_counts_sessions(tmp_path: Path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "note.txt").write_text("hello\n")
    data = tmp_path / "data"
    create_session(source=source, name="box", data_dir=data)
    report = storage_report(data)
    assert report.session_count == 1
    assert report.total_bytes > 0
    assert report.largest[0].name == "box"

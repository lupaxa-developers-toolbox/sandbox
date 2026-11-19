"""Apply writes back only when the source still matches the original snapshot."""

from __future__ import annotations

import json
from pathlib import Path

from lupaxa.sandbox.operations.apply import apply_changes
from lupaxa.sandbox.service import create_session


def _project(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "project"
    source.mkdir()
    (source / "note.txt").write_text("v1\n")
    (source / "drop.txt").write_text("gone\n")
    data = tmp_path / "data"
    create_session(source=source, name="box", data_dir=data)
    return source, data


def test_apply_updates_an_unchanged_source_and_keeps_a_journal(tmp_path: Path) -> None:
    source, data = _project(tmp_path)
    workspace = data / "sessions"
    note = next(workspace.rglob("workspace/project/note.txt"))
    note.write_text("v3\n")
    result = apply_changes(name="box", data_dir=data, select_all=True)
    assert (source / "note.txt").read_text() == "v3\n"
    assert result.journal is not None
    journal = json.loads((result.journal / "journal.json").read_text())
    assert journal["atomic"] is False
    assert journal["status"] == "completed"
    backup = result.journal / "backup" / "note.txt"
    assert backup.read_text() == "v1\n"


def test_conflict_leaves_the_edited_source_in_place(tmp_path: Path) -> None:
    source, data = _project(tmp_path)
    note = next((data / "sessions").rglob("workspace/project/note.txt"))
    note.write_text("v3\n")
    (source / "note.txt").write_text("v2\n")
    result = apply_changes(name="box", data_dir=data, select_all=True)
    assert (source / "note.txt").read_text() == "v2\n"
    assert result.conflicts[0].path == "note.txt"
    assert result.journal is None


def test_delete_requires_an_explicit_flag(tmp_path: Path) -> None:
    source, data = _project(tmp_path)
    drop = next((data / "sessions").rglob("workspace/project/drop.txt"))
    drop.unlink()
    held = apply_changes(name="box", data_dir=data, select_all=True)
    assert (source / "drop.txt").is_file()
    assert any(item.reason == "delete needs --delete" for item in held.items)
    apply_changes(name="box", data_dir=data, select_all=True, allow_delete=True)
    assert not (source / "drop.txt").exists()


def test_dry_run_does_not_change_the_source(tmp_path: Path) -> None:
    source, data = _project(tmp_path)
    note = next((data / "sessions").rglob("workspace/project/note.txt"))
    note.write_text("v3\n")
    apply_changes(name="box", data_dir=data, select_all=True, dry_run=True)
    assert (source / "note.txt").read_text() == "v1\n"


def test_include_limits_the_write(tmp_path: Path) -> None:
    source, data = _project(tmp_path)
    workspace = next((data / "sessions").rglob("workspace/project"))
    (workspace / "note.txt").write_text("v3\n")
    (workspace / "drop.txt").write_text("edited\n")
    apply_changes(name="box", data_dir=data, include=["note.txt"])
    assert (source / "note.txt").read_text() == "v3\n"
    assert (source / "drop.txt").read_text() == "gone\n"

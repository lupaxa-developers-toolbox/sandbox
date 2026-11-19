"""Export copies selected files and refuses to follow escaping links."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lupaxa.sandbox.exceptions import UsageError
from lupaxa.sandbox.operations.export import export_files
from lupaxa.sandbox.operations.paths import safe_join
from lupaxa.sandbox.service import create_session


def _session(tmp_path: Path):
    source = tmp_path / "project"
    (source / "dist").mkdir(parents=True)
    (source / "dist" / "pkg.whl").write_bytes(b"wheel")
    (source / "keep.txt").write_text("stay\n")
    return create_session(source=source, name="box", data_dir=tmp_path / "data")


def test_export_copies_a_nested_match_and_leaves_the_workspace(tmp_path: Path) -> None:
    session = _session(tmp_path)
    destination = tmp_path / "out"
    result = export_files(
        name="box",
        data_dir=tmp_path / "data",
        include=["dist/**"],
        destination=destination,
    )
    assert (destination / "dist" / "pkg.whl").read_bytes() == b"wheel"
    assert (session.workspace / "dist" / "pkg.whl").read_bytes() == b"wheel"
    assert not (destination / "keep.txt").exists()
    assert result.items[0].action == "create"


def test_dry_run_does_not_create_the_destination(tmp_path: Path) -> None:
    _session(tmp_path)
    destination = tmp_path / "out"
    export_files(
        name="box",
        data_dir=tmp_path / "data",
        include=["dist/**"],
        destination=destination,
        dry_run=True,
    )
    assert not destination.exists()


def test_existing_file_is_kept_until_confirmed(tmp_path: Path) -> None:
    _session(tmp_path)
    destination = tmp_path / "out"
    target = destination / "dist" / "pkg.whl"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    past = target.stat().st_mtime - 10
    os.utime(target, (past, past))
    skipped = export_files(
        name="box",
        data_dir=tmp_path / "data",
        include=["dist/**"],
        destination=destination,
    )
    assert target.read_bytes() == b"old"
    assert skipped.items[0].action == "skip-exists"
    export_files(
        name="box",
        data_dir=tmp_path / "data",
        include=["dist/**"],
        destination=destination,
        yes=True,
    )
    assert target.read_bytes() == b"wheel"


def test_newer_destination_is_not_replaced_without_force(tmp_path: Path) -> None:
    session = _session(tmp_path)
    destination = tmp_path / "out"
    target = destination / "dist" / "pkg.whl"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"newer")
    future = (session.workspace / "dist" / "pkg.whl").stat().st_mtime + 100
    os.utime(target, (future, future))
    result = export_files(
        name="box",
        data_dir=tmp_path / "data",
        include=["dist/**"],
        destination=destination,
        yes=True,
    )
    assert target.read_bytes() == b"newer"
    assert result.items[0].action == "skip-newer"


def test_outside_link_is_rejected_before_copy(tmp_path: Path) -> None:
    session = _session(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret\n")
    (session.workspace / "jump").symlink_to(outside)
    destination = tmp_path / "out"
    with pytest.raises(UsageError, match="outside"):
        export_files(
            name="box",
            data_dir=tmp_path / "data",
            include=["jump"],
            destination=destination,
        )
    assert not destination.exists()
    assert outside.read_text() == "secret\n"


def test_safe_join_rejects_parent_segments(tmp_path: Path) -> None:
    with pytest.raises(UsageError):
        safe_join(tmp_path, "../outside.txt")

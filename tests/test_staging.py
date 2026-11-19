"""Copy a project into a workspace without touching the original."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lupaxa.sandbox.exceptions import StagingError
from lupaxa.sandbox.workspace.staging import stage_copy


def _project(root: Path) -> Path:
    source = root / "project"
    (source / "src").mkdir(parents=True)
    (source / "src" / "app.py").write_text("print('ok')\n")
    (source / "src" / "app.py").chmod(0o755)
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("git\n")
    (source / "cache.pyc").write_bytes(b"pyc")
    outside = root / "outside.txt"
    outside.write_text("secret\n")
    (source / "escape").symlink_to(outside)
    (source / "inside").symlink_to(Path("src") / "app.py")
    return source


def test_copy_preserves_files_modes_and_links_and_skips_excludes(tmp_path: Path) -> None:
    source = _project(tmp_path)
    before = (source / "src" / "app.py").read_bytes()
    before_stat = (source / "src" / "app.py").stat()
    destination = tmp_path / "workspace"

    staged = stage_copy(
        source,
        destination,
        exclude=[".git/", "*.pyc"],
        max_files=100,
        max_size_mb=10,
    )

    copied = destination / "src" / "app.py"
    assert copied.read_bytes() == before
    assert copied.stat().st_mode & 0o111
    assert not (destination / ".git").exists()
    assert not (destination / "cache.pyc").exists()
    assert (destination / "escape").is_symlink()
    assert os.readlink(destination / "escape") == os.readlink(source / "escape")
    assert (destination / "inside").is_symlink()
    assert staged.outside_links == ["escape"]
    assert (source / "src" / "app.py").read_bytes() == before
    assert (source / "src" / "app.py").stat().st_mtime_ns == before_stat.st_mtime_ns


def test_limit_removes_incomplete_workspace(tmp_path: Path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "a.txt").write_text("a")
    (source / "b.txt").write_text("b")
    destination = tmp_path / "workspace"

    with pytest.raises(StagingError):
        stage_copy(source, destination, exclude=[], max_files=1, max_size_mb=10)

    assert not destination.exists()
    assert (source / "a.txt").read_text() == "a"


def test_source_must_be_a_directory(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(StagingError):
        stage_copy(missing, tmp_path / "out", exclude=[], max_files=10, max_size_mb=1)

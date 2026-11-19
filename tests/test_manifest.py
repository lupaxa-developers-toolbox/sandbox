"""Filesystem manifests hash contents and do not follow links."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from lupaxa.sandbox.workspace.manifest import build_manifest


def test_manifest_hashes_files_and_records_link_targets(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    payload = b"version = 1\n"
    (tmp_path / "src" / "version.py").write_bytes(payload)
    outside = tmp_path.parent / "other-target"
    outside.write_text("other\n")
    (tmp_path / "jump").symlink_to(outside)

    manifest = build_manifest(tmp_path, exclude=[])
    version = manifest.entries["src/version.py"]
    link = manifest.entries["jump"]
    directory = manifest.entries["src"]

    assert version.type == "file"
    assert version.sha256 == hashlib.sha256(payload).hexdigest()
    assert version.size == len(payload)
    assert link.type == "symlink"
    assert link.target == os.readlink(tmp_path / "jump")
    assert link.sha256 is None
    assert directory.type == "directory"
    assert manifest.incomplete is False


def test_unreadable_file_marks_the_manifest_incomplete(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "secret.txt").write_text("hidden\n")

    real_open = open

    def deny(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if str(path).endswith("secret.txt") and "r" in str(mode):
            raise PermissionError("denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", deny)
    manifest = build_manifest(tmp_path, exclude=[])

    assert manifest.incomplete is True
    assert manifest.errors

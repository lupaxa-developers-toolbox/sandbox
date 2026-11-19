"""Change sets compare two manifests."""

from __future__ import annotations

from lupaxa.sandbox.workspace.changes import compare_manifests
from lupaxa.sandbox.workspace.manifest import Entry, Manifest


def _entry(path: str, **overrides: object) -> Entry:
    data: dict[str, object] = {
        "path": path,
        "type": "file",
        "size": 1,
        "sha256": "a" * 64,
        "mode": "0644",
        "modified_ns": 1,
        "target": None,
    }
    data.update(overrides)
    return Entry(**data)  # type: ignore[arg-type]


def _manifest(*entries: Entry, incomplete: bool = False) -> Manifest:
    return Manifest(
        entries={entry.path: entry for entry in entries},
        incomplete=incomplete,
        errors=["unreadable"] if incomplete else [],
    )


def test_classifies_add_modify_delete_mode_link_and_type() -> None:
    before = _manifest(
        _entry("kept.py", sha256="a" * 64),
        _entry("edited.py", sha256="b" * 64),
        _entry("gone.py"),
        _entry("mode.py", sha256="c" * 64, mode="0644"),
        _entry("link", type="symlink", sha256=None, size=None, target="a"),
        _entry("kind.py", type="file"),
    )
    after = _manifest(
        _entry("kept.py", sha256="a" * 64),
        _entry("edited.py", sha256="d" * 64),
        _entry("mode.py", sha256="c" * 64, mode="0755"),
        _entry("link", type="symlink", sha256=None, size=None, target="b"),
        _entry("kind.py", type="directory", sha256=None, size=None),
        _entry("new.py", sha256="e" * 64),
    )

    changes = compare_manifests(before, after, exclude=[])
    found = {change.path: change.category for change in changes.changes}

    assert "kept.py" not in found
    assert found["edited.py"] == "modified"
    assert found["gone.py"] == "deleted"
    assert found["new.py"] == "added"
    assert found["mode.py"] == "permissions_changed"
    assert found["link"] == "link_changed"
    assert found["kind.py"] == "type_changed"
    assert changes.incomplete is False


def test_tracking_exclude_hides_a_staged_file() -> None:
    before = _manifest()
    after = _manifest(_entry("pkg/__pycache__/mod.pyc"), _entry("out.txt"))

    changes = compare_manifests(before, after, exclude=["__pycache__/", "*.pyc"])

    assert [change.path for change in changes.changes] == ["out.txt"]


def test_incomplete_manifest_does_not_look_like_zero_changes() -> None:
    before = _manifest(incomplete=True)
    after = _manifest(incomplete=True)
    changes = compare_manifests(before, after, exclude=[])
    assert changes.incomplete is True
    assert changes.changes == []

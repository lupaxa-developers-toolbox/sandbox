"""Two environments start from one snapshot and their results are compared."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from lupaxa.sandbox.compare import compare_outputs, compare_recipe
from lupaxa.sandbox.sessions.store import SessionStore
from lupaxa.sandbox.workspace.changes import Change
from lupaxa.sandbox.workspace.manifest import Entry, Manifest


def _entry(path: str, digest: str, mode: str = "0644", size: int = 1) -> Entry:
    return Entry(path, "file", size, digest, mode, 0, None)


def _manifest(*entries: Entry) -> Manifest:
    return Manifest({entry.path: entry for entry in entries}, False, [])


def test_output_comparison_reports_content_without_calling_it_incompatible() -> None:
    left = _manifest(_entry("dist/package.whl", "aaa"), _entry("docs/api.md", "same"))
    right = _manifest(_entry("dist/package.whl", "bbb"), _entry("docs/api.md", "same"))
    changes = [Change("added", "dist/package.whl"), Change("added", "docs/api.md")]
    rows = compare_outputs(left, changes, right, changes)
    by_path = {row.path: row.status for row in rows}
    assert by_path["dist/package.whl"] == "content differs"
    assert by_path["docs/api.md"] == "identical"
    assert rows[0].note
    assert "incompatible" not in rows[0].note.lower()


def test_output_comparison_reports_presence_and_permissions() -> None:
    left = _manifest(_entry("only-a.txt", "aaa"), _entry("mode.txt", "same", mode="0644"))
    right = _manifest(_entry("only-b.txt", "bbb"), _entry("mode.txt", "same", mode="0755"))
    changes_a = [Change("added", "only-a.txt"), Change("added", "mode.txt")]
    changes_b = [Change("added", "only-b.txt"), Change("added", "mode.txt")]
    by_path = {row.path: row.status for row in compare_outputs(left, changes_a, right, changes_b)}
    assert by_path["only-a.txt"] == "only in A"
    assert by_path["only-b.txt"] == "only in B"
    assert by_path["mode.txt"] == "permissions differ"


def test_compare_recipe_uses_one_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "note.txt").write_text("one\n")
    recipe = project / "sandbox.yml"
    recipe.write_text(
        f"""
version: 1
sandbox:
  name: side
execution:
  commands:
    - id: write
      run: ["{sys.executable}", "-c", "open('out.txt','w').write('same')"]
"""
    )
    data = tmp_path / "data"
    real = __import__("lupaxa.sandbox.service", fromlist=["create_session"]).create_session
    calls: list[Path] = []

    def wrapped(**kwargs: Any) -> Any:
        if len(calls) == 0:
            (project / "note.txt").write_text("mutated\n")
        source = kwargs["copy_from"] if kwargs.get("copy_from") is not None else kwargs["source"]
        calls.append(Path(str(source)))
        return real(**kwargs)

    monkeypatch.setattr("lupaxa.sandbox.recipes.runner.create_session", wrapped)
    report = compare_recipe(recipe, data_dir=data)
    assert report.files["out.txt"] == "identical"
    assert report.exit_code_a == 0
    assert report.exit_code_b == 0
    assert report.stdout == "identical"
    assert "incompatible" not in report.note.lower()
    store = SessionStore(data)
    assert (store.get("side-a").workspace / "note.txt").read_text() == "one\n"
    assert (store.get("side-b").workspace / "note.txt").read_text() == "one\n"
    assert calls[0] == calls[1]
    assert calls[0] != project.resolve()

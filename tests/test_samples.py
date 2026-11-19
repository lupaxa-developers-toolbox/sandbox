"""The documented samples validate and the smallest one runs."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lupaxa.sandbox.recipes.runner import run_recipe_file, validate_file
from lupaxa.sandbox.sessions.store import SessionStore

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def test_sample_recipes_validate() -> None:
    paths = sorted(SAMPLES.glob("*/sandbox.yml"))
    assert [path.parent.name for path in paths] == ["compare-notes", "hello", "report"]
    for path in paths:
        validate_file(path)


def test_hello_sample_writes_a_file_and_leaves_the_source(tmp_path: Path) -> None:
    if shutil.which("python3") is None:
        pytest.skip("python3 is not on PATH")
    source_note = (SAMPLES / "hello" / "note.txt").read_text()
    data = tmp_path / "data"
    result = run_recipe_file(SAMPLES / "hello" / "sandbox.yml", data_dir=data)
    assert result.outcomes[0].status == "completed"
    workspace = SessionStore(data).get("hello").workspace
    assert (workspace / "generated.txt").read_text() == "ok\n"
    assert (workspace / "note.txt").read_text() == source_note
    assert (SAMPLES / "hello" / "note.txt").read_text() == source_note
    assert not (SAMPLES / "hello" / "generated.txt").exists()

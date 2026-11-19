"""Git-style staging and tracking patterns."""

from __future__ import annotations

from lupaxa.sandbox.workspace.patterns import is_excluded


def test_pyc_matches_in_nested_directories() -> None:
    patterns = ["*.pyc"]
    assert is_excluded(patterns, "mod.pyc", is_dir=False)
    assert is_excluded(patterns, "pkg/mod.pyc", is_dir=False)
    assert not is_excluded(patterns, "mod.py", is_dir=False)


def test_directory_pattern_matches_the_directory_and_its_children() -> None:
    patterns = [".git/"]
    assert is_excluded(patterns, ".git", is_dir=True)
    assert is_excluded(patterns, ".git/config", is_dir=False)
    assert not is_excluded(patterns, "git", is_dir=True)
    assert not is_excluded(patterns, "src/app.py", is_dir=False)


def test_empty_patterns_exclude_nothing() -> None:
    assert not is_excluded([], "src/app.py", is_dir=False)

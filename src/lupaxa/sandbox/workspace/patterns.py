"""Git-style path matching for staging and tracking."""

from __future__ import annotations

import pathspec

DEFAULT_STAGING_EXCLUDES = [".git/", ".venv/", "__pycache__/", "*.pyc"]
DEFAULT_TRACKING_EXCLUDES = [".venv/", "__pycache__/", "*.pyc"]


def is_excluded(patterns: list[str], relative: str, *, is_dir: bool) -> bool:
    """Return whether ``relative`` matches any gitwildmatch pattern.

    A trailing slash matches a directory. A match on a parent directory
    excludes its children, which is how ``.git/`` drops ``.git/config``.
    """
    return _matches(patterns, relative, is_dir=is_dir)


def is_included(patterns: list[str], relative: str, *, is_dir: bool) -> bool:
    """Return whether ``relative`` is selected by an include pattern."""
    return _matches(patterns, relative, is_dir=is_dir)


def _matches(patterns: list[str], relative: str, *, is_dir: bool) -> bool:
    if not patterns:
        return False
    path = relative.strip("/")
    if not path or path == ".":
        return False
    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    candidates = [path]
    if is_dir:
        candidates.append(path + "/")
    parts = path.split("/")
    for index in range(len(parts) - 1):
        parent = "/".join(parts[: index + 1])
        candidates.append(parent)
        candidates.append(parent + "/")
    return any(spec.match_file(candidate) for candidate in candidates)

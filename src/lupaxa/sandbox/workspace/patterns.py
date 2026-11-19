"""Git-style path matching for staging and tracking."""

from __future__ import annotations

import pathspec

DEFAULT_STAGING_EXCLUDES = [".git/", ".venv/", "__pycache__/", "*.pyc"]
DEFAULT_TRACKING_EXCLUDES = [".venv/", "__pycache__/", "*.pyc"]


def is_excluded(patterns: list[str], relative: str, *, is_dir: bool) -> bool:
    """Return whether ``relative`` matches any gitignore pattern.

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
    spec = _compile(patterns)
    candidates = [path]
    if is_dir:
        candidates.append(path + "/")
    parts = path.split("/")
    for index in range(len(parts) - 1):
        parent = "/".join(parts[: index + 1])
        candidates.append(parent)
        candidates.append(parent + "/")
    return any(spec.match_file(candidate) for candidate in candidates)


def _compile(patterns: list[str]) -> pathspec.PathSpec:
    """Compile gitignore patterns.

    ``gitwildmatch`` is the pathspec 0.12 name. pathspec 1.0 deprecates it
    and warns on every pattern, so newer releases use ``GitIgnoreSpec``.
    """
    factory = getattr(pathspec, "GitIgnoreSpec", None)
    if factory is not None:
        compiled = factory.from_lines(patterns)
        if isinstance(compiled, pathspec.PathSpec):
            return compiled
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)

"""Keep export and apply paths inside the directory the user named."""

from __future__ import annotations

import os
from pathlib import Path

from lupaxa.sandbox.exceptions import UsageError


def safe_join(root: Path, relative: str) -> Path:
    """Join ``relative`` onto ``root`` and reject paths that escape it.

    The check is lexical for the relative part, then resolves an existing
    parent so a symlink cannot redirect the write outside ``root``.
    """
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise UsageError(f"Path escapes the selected root: {relative}")
    root_resolved = root.resolve()
    candidate = root_resolved.joinpath(*Path(relative).parts)
    parent = candidate.parent
    if parent.exists():
        parent_resolved = parent.resolve()
        try:
            parent_resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise UsageError(f"Path escapes the selected root: {relative}") from exc
        return parent_resolved / candidate.name
    normalised = Path(os.path.normpath(candidate))
    try:
        normalised.relative_to(root_resolved)
    except ValueError as exc:
        raise UsageError(f"Path escapes the selected root: {relative}") from exc
    return normalised


def link_escapes(path: Path, root: Path) -> bool:
    """Return whether a symlink points outside ``root``. Regular files do not."""
    if not path.is_symlink():
        return False
    raw = Path(os.readlink(path))
    target = raw if raw.is_absolute() else path.parent / raw
    try:
        target.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        return True
    return False

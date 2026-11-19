"""Unified diffs that refuse to decode binary bytes."""

from __future__ import annotations

import difflib
from dataclasses import dataclass

DEFAULT_MAX_DIFF_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class FileDiff:
    """A text diff, a binary marker, or a skipped large file."""

    binary: bool
    text: str
    skipped: bool


def file_diff(
    path: str,
    before: bytes,
    after: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_DIFF_BYTES,
) -> FileDiff:
    """Diff two blobs. NUL bytes and invalid UTF-8 are reported as binary."""
    if len(before) > max_bytes or len(after) > max_bytes:
        return FileDiff(binary=False, text="", skipped=True)
    if _is_binary(before) or _is_binary(after):
        return FileDiff(binary=True, text="", skipped=False)
    old = before.decode("utf-8").splitlines()
    new = after.decode("utf-8").splitlines()
    lines = difflib.unified_diff(old, new, fromfile=path, tofile=path, lineterm="")
    return FileDiff(binary=False, text="\n".join(lines), skipped=False)


def _is_binary(payload: bytes) -> bool:
    if b"\0" in payload:
        return True
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False

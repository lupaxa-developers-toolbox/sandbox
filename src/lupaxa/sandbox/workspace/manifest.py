"""Snapshot a workspace without following symbolic links."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from lupaxa.sandbox.workspace.patterns import is_excluded
from lupaxa.sandbox.workspace.staging import _relative

_CHUNK = 1024 * 1024


@dataclass
class Entry:
    """One manifest record. ``sha256`` is set only for regular files."""

    path: str
    type: str
    size: int | None
    sha256: str | None
    mode: str | None
    modified_ns: int | None
    target: str | None


@dataclass
class Manifest:
    """A filesystem snapshot. ``incomplete`` means a read failed."""

    entries: dict[str, Entry]
    incomplete: bool
    errors: list[str]

    def to_dict(self) -> dict[str, object]:
        ordered = sorted(self.entries.values(), key=lambda item: item.path)
        files = [asdict(entry) for entry in ordered]
        return {"incomplete": self.incomplete, "errors": list(self.errors), "files": files}

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Manifest:
        files = payload.get("files", [])
        entries: dict[str, Entry] = {}
        if isinstance(files, list):
            for item in files:
                if not isinstance(item, dict):
                    continue
                entry = Entry(
                    path=str(item["path"]),
                    type=str(item["type"]),
                    size=item.get("size") if isinstance(item.get("size"), int) else None,
                    sha256=item.get("sha256") if isinstance(item.get("sha256"), str) else None,
                    mode=item.get("mode") if isinstance(item.get("mode"), str) else None,
                    modified_ns=item.get("modified_ns")
                    if isinstance(item.get("modified_ns"), int)
                    else None,
                    target=item.get("target") if isinstance(item.get("target"), str) else None,
                )
                entries[entry.path] = entry
        errors = payload.get("errors", [])
        return cls(
            entries=entries,
            incomplete=bool(payload.get("incomplete")),
            errors=[str(item) for item in errors] if isinstance(errors, list) else [],
        )


def build_manifest(root: Path, exclude: list[str]) -> Manifest:
    """Hash regular files and record links by their target, not their contents."""
    root = Path(root)
    entries: dict[str, Entry] = {}
    errors: list[str] = []
    incomplete = False
    if not root.is_dir():
        return Manifest(entries, True, [f"Workspace is missing: {root}"])

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        rel_dir = current.relative_to(root)
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            rel = _relative(rel_dir, name)
            if child.is_symlink():
                _add_link(entries, child, rel)
                continue
            if is_excluded(exclude, rel, is_dir=True):
                continue
            kept.append(name)
            entries[rel] = _directory(child, rel)
        dirnames[:] = kept
        for name in filenames:
            child = current / name
            rel = _relative(rel_dir, name)
            if is_excluded(exclude, rel, is_dir=False):
                continue
            if child.is_symlink():
                _add_link(entries, child, rel)
                continue
            info = child.lstat()
            if not stat.S_ISREG(info.st_mode):
                entries[rel] = Entry(rel, "other", None, None, _mode(info), info.st_mtime_ns, None)
                continue
            digest, error = _hash(child)
            if error is not None:
                incomplete = True
                errors.append(f"{rel}: {error}")
            entries[rel] = Entry(
                rel,
                "file",
                info.st_size,
                digest,
                _mode(info),
                info.st_mtime_ns,
                None,
            )
    return Manifest(entries, incomplete, errors)


def _directory(path: Path, rel: str) -> Entry:
    info = path.lstat()
    return Entry(rel, "directory", None, None, _mode(info), info.st_mtime_ns, None)


def _add_link(entries: dict[str, Entry], path: Path, rel: str) -> None:
    info = path.lstat()
    entries[rel] = Entry(
        rel,
        "symlink",
        None,
        None,
        _mode(info),
        info.st_mtime_ns,
        os.readlink(path),
    )


def _mode(info: os.stat_result) -> str:
    return f"{stat.S_IMODE(info.st_mode):04o}"


def _hash(path: Path) -> tuple[str | None, str | None]:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        return None, str(exc)
    return digest.hexdigest(), None

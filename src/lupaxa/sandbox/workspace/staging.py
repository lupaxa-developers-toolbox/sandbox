"""Copy a source tree into a disposable workspace."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from lupaxa.sandbox.exceptions import StagingError
from lupaxa.sandbox.workspace.patterns import is_excluded

_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class StageResult:
    """What staging copied, and which links point outside the source tree."""

    outside_links: list[str]
    file_count: int
    byte_count: int


def stage_copy(
    source: Path,
    destination: Path,
    *,
    exclude: list[str],
    max_files: int,
    max_size_mb: int,
) -> StageResult:
    """Copy ``source`` into ``destination`` without following links.

    The source tree is only read. On a limit or copy failure the incomplete
    destination is removed.
    """
    source = Path(source)
    destination = Path(destination)
    if not source.is_dir():
        raise StagingError(f"Source is not a directory: {source}")
    if destination.exists():
        raise StagingError(f"Workspace already exists: {destination}")

    max_bytes = max_size_mb * 1024 * 1024
    outside: list[str] = []
    count = 0
    total = 0
    destination.mkdir(parents=True)
    try:
        for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
            current = Path(dirpath)
            rel_dir = current.relative_to(source)
            kept: list[str] = []
            for name in dirnames:
                child = current / name
                rel = _relative(rel_dir, name)
                if child.is_symlink():
                    count, total = _account(count, total, 0, max_files, max_bytes)
                    _copy_link(child, destination / rel, source, outside, rel)
                    continue
                if is_excluded(exclude, rel, is_dir=True):
                    continue
                kept.append(name)
                (destination / rel).mkdir(parents=True, exist_ok=True)
            dirnames[:] = kept
            for name in filenames:
                child = current / name
                rel = _relative(rel_dir, name)
                if is_excluded(exclude, rel, is_dir=False):
                    continue
                if child.is_symlink():
                    count, total = _account(count, total, 0, max_files, max_bytes)
                    _copy_link(child, destination / rel, source, outside, rel)
                    continue
                if not child.is_file():
                    continue
                size = child.stat().st_size
                count, total = _account(count, total, size, max_files, max_bytes)
                target = destination / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with child.open("rb") as incoming, target.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=_CHUNK)
                shutil.copystat(child, target, follow_symlinks=False)
    except StagingError:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise StagingError(f"Staging failed: {exc}") from exc

    outside.sort()
    return StageResult(outside_links=outside, file_count=count, byte_count=total)


def _account(count: int, total: int, size: int, max_files: int, max_bytes: int) -> tuple[int, int]:
    count += 1
    total += size
    if count > max_files or total > max_bytes:
        raise StagingError(
            f"Staging exceeded the workspace limit ({max_files} files or {max_bytes} bytes)"
        )
    return count, total


def _relative(rel_dir: Path, name: str) -> str:
    if rel_dir == Path("."):
        return name
    return (rel_dir / name).as_posix()


def _copy_link(source: Path, destination: Path, root: Path, outside: list[str], rel: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(os.readlink(source), destination)
    if _points_outside(source, root):
        outside.append(rel)


def _points_outside(link: Path, root: Path) -> bool:
    raw = Path(os.readlink(link))
    target = raw if raw.is_absolute() else link.parent / raw
    try:
        target.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        return True
    return False

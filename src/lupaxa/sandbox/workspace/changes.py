"""Classify differences between two manifests."""

from __future__ import annotations

from dataclasses import dataclass

from lupaxa.sandbox.workspace.manifest import Entry, Manifest
from lupaxa.sandbox.workspace.patterns import is_excluded


@dataclass(frozen=True)
class Change:
    """One path-level difference."""

    category: str
    path: str


@dataclass
class ChangeSet:
    """Differences plus whether either snapshot could not be read fully."""

    changes: list[Change]
    incomplete: bool
    errors: list[str]

    def counts(self) -> dict[str, int]:
        summary = {
            "added": 0,
            "modified": 0,
            "deleted": 0,
            "permissions_changed": 0,
            "link_changed": 0,
            "type_changed": 0,
        }
        for change in self.changes:
            summary[change.category] = summary.get(change.category, 0) + 1
        return summary


def compare_manifests(before: Manifest, after: Manifest, exclude: list[str]) -> ChangeSet:
    """Compare snapshots. Content changes take precedence over mode-only changes."""
    changes: list[Change] = []
    paths = set(before.entries) | set(after.entries)
    for path in sorted(paths):
        old = before.entries.get(path)
        new = after.entries.get(path)
        sample = new or old
        assert sample is not None
        if is_excluded(exclude, path, is_dir=sample.type == "directory"):
            continue
        category = _category(old, new)
        if category is not None:
            changes.append(Change(category, path))
    return ChangeSet(
        changes=changes,
        incomplete=before.incomplete or after.incomplete,
        errors=[*before.errors, *after.errors],
    )


def _category(old: Entry | None, new: Entry | None) -> str | None:
    if old is None:
        return "added"
    if new is None:
        return "deleted"
    if old.type != new.type:
        return "type_changed"
    if old.type == "symlink" and old.target != new.target:
        return "link_changed"
    if old.type == "file" and old.sha256 != new.sha256:
        return "modified"
    if old.mode != new.mode:
        return "permissions_changed"
    return None

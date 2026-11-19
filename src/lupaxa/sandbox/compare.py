"""Compare two runs that started from the same staged snapshot."""

from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from lupaxa.sandbox.exceptions import UsageError
from lupaxa.sandbox.execution.environment import ensure_interpreter
from lupaxa.sandbox.recipes.models import Recipe
from lupaxa.sandbox.recipes.runner import prepare_recipe, run_resolved_recipe
from lupaxa.sandbox.service import SECURITY_NOTICE
from lupaxa.sandbox.sessions.store import Session, SessionStore
from lupaxa.sandbox.workspace.changes import Change, compare_manifests
from lupaxa.sandbox.workspace.manifest import Entry, Manifest, build_manifest
from lupaxa.sandbox.workspace.patterns import DEFAULT_TRACKING_EXCLUDES
from lupaxa.sandbox.workspace.staging import stage_copy

OBSERVED_NOTE = (
    "Observed differences are reported as results. Sandbox does not treat them as a verdict."
)


@dataclass(frozen=True)
class OutputDifference:
    """How one path compares between the two workspaces."""

    path: str
    status: str
    note: str


@dataclass
class ComparisonReport:
    """Exit status, output, and file differences for two sessions."""

    session_a: str
    session_b: str
    python_a: str
    python_b: str
    exit_code_a: int | None
    exit_code_b: int | None
    duration_a: float
    duration_b: float
    stdout: str
    stderr: str
    files: dict[str, str]
    note: str
    security_notice: str
    timed_out: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "session_a": self.session_a,
            "session_b": self.session_b,
            "python_a": self.python_a,
            "python_b": self.python_b,
            "exit_code_a": self.exit_code_a,
            "exit_code_b": self.exit_code_b,
            "duration_a": self.duration_a,
            "duration_b": self.duration_b,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "files": dict(self.files),
            "note": self.note,
            "security_notice": self.security_notice,
            "timed_out": self.timed_out,
        }


def compare_outputs(
    manifest_a: Manifest,
    changes_a: list[Change],
    manifest_b: Manifest,
    changes_b: list[Change],
) -> list[OutputDifference]:
    """Compare paths that either run added, modified, or deleted."""
    paths = {change.path for change in changes_a} | {change.path for change in changes_b}
    rows: list[OutputDifference] = []
    for path in sorted(paths):
        status = _file_status(manifest_a.entries.get(path), manifest_b.entries.get(path))
        rows.append(OutputDifference(path, status, OBSERVED_NOTE))
    return rows


def compare_recipe(
    path: Path,
    *,
    data_dir: Path,
    python_a: str | None = None,
    python_b: str | None = None,
    stream: bool = False,
) -> ComparisonReport:
    """Stage the source once, run the recipe twice, and compare the results."""
    if (python_a is None) != (python_b is None):
        raise UsageError("Pass both --python-a and --python-b, or neither")
    if python_a is not None and python_b is not None:
        ensure_interpreter(python_a)
        ensure_interpreter(python_b)
    recipe, source = prepare_recipe(path)
    base = recipe.sandbox.name or "compare-" + uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix="sandbox-snapshot-") as temporary:
        snapshot = Path(temporary) / "tree"
        stage_copy(
            source,
            snapshot,
            exclude=list(recipe.workspace.staging.exclude),
            max_files=recipe.workspace.limits.max_files,
            max_size_mb=recipe.workspace.limits.max_size_mb,
        )
        run_resolved_recipe(
            _side(recipe, f"{base}-a", python_a),
            source,
            data_dir,
            stream=stream,
            copy_from=snapshot,
        )
        run_resolved_recipe(
            _side(recipe, f"{base}-b", python_b),
            source,
            data_dir,
            stream=stream,
            copy_from=snapshot,
        )
    return compare_sessions(data_dir, f"{base}-a", f"{base}-b", python_a or "", python_b or "")


def compare_sessions(
    data_dir: Path,
    name_a: str,
    name_b: str,
    python_a: str = "",
    python_b: str = "",
) -> ComparisonReport:
    """Compare two existing sessions with the same rules as a fresh comparison."""
    store = SessionStore(data_dir)
    session_a = store.get(name_a)
    session_b = store.get(name_b)
    excludes = list(DEFAULT_TRACKING_EXCLUDES)
    changes_a, manifest_a = _changes(store, session_a, excludes)
    changes_b, manifest_b = _changes(store, session_b, excludes)
    rows = compare_outputs(manifest_a, changes_a, manifest_b, changes_b)
    text_a = _stream(session_a, "stdout"), _stream(session_a, "stderr")
    text_b = _stream(session_b, "stdout"), _stream(session_b, "stderr")
    return ComparisonReport(
        session_a=session_a.name,
        session_b=session_b.name,
        python_a=_python_label(session_a, python_a),
        python_b=_python_label(session_b, python_b),
        exit_code_a=_exit_code(session_a),
        exit_code_b=_exit_code(session_b),
        duration_a=_duration(session_a),
        duration_b=_duration(session_b),
        stdout="identical" if text_a[0] == text_b[0] else "differs",
        stderr="identical" if text_a[1] == text_b[1] else "differs",
        files={row.path: row.status for row in rows},
        note=OBSERVED_NOTE,
        security_notice=SECURITY_NOTICE,
        timed_out=_timed_out(session_a) or _timed_out(session_b),
    )


def render_comparison(report: ComparisonReport) -> str:
    """Plain-text comparison. Differences are observations, not a verdict."""
    lines = [
        "Sandbox comparison",
        "",
        f"Environment A  {report.session_a}",
        f"Python {report.python_a or 'recipe default'}",
        f"Exit {report.exit_code_a}  duration {report.duration_a:.3f}s",
        "",
        f"Environment B  {report.session_b}",
        f"Python {report.python_b or 'recipe default'}",
        f"Exit {report.exit_code_b}  duration {report.duration_b:.3f}s",
        "",
        f"stdout {report.stdout}",
        f"stderr {report.stderr}",
        "",
        "Output differences",
    ]
    if not report.files:
        lines.append("(none)")
    for path, status in report.files.items():
        lines.append(f"{path} — {status}")
    lines.extend(["", report.note, report.security_notice])
    return "\n".join(lines)


def _side(recipe: Recipe, name: str, python: str | None) -> Recipe:
    side = recipe.model_copy(deep=True)
    side.sandbox.name = name
    if python:
        side.environment.python = python
        if side.environment.type == "none":
            side.environment.type = "existing"
    return side


def _changes(
    store: SessionStore,
    session: Session,
    excludes: list[str],
) -> tuple[list[Change], Manifest]:
    current = build_manifest(session.workspace, exclude=[])
    change_set = compare_manifests(store.baseline(session), current, excludes)
    return change_set.changes, current


def _file_status(left: Entry | None, right: Entry | None) -> str:
    if left is None and right is None:
        return "deleted in both"
    if left is None:
        return "only in B"
    if right is None:
        return "only in A"
    if left.type != right.type or left.sha256 != right.sha256 or left.target != right.target:
        return "content differs"
    if left.mode != right.mode:
        return "permissions differ"
    if left.size != right.size:
        return "size differs"
    return "identical"


def _stream(session: Session, name: str) -> str:
    return "".join(getattr(record, name) for record in session.executions)


def _exit_code(session: Session) -> int | None:
    if not session.executions:
        return None
    return session.executions[-1].exit_code


def _duration(session: Session) -> float:
    return sum(record.duration_seconds for record in session.executions)


def _timed_out(session: Session) -> bool:
    return any(record.termination_reason == "timeout" for record in session.executions)


def _python_label(session: Session, fallback: str) -> str:
    path = session.root / "environment.json"
    if not path.is_file():
        return fallback
    payload = json.loads(path.read_text())
    python = payload.get("python") if isinstance(payload, dict) else None
    if isinstance(python, str) and python:
        return python
    return fallback

"""Human and JSON views of a session report."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from lupaxa.sandbox.service import Report
from lupaxa.sandbox.workspace.changes import Change, ChangeSet

_SYMBOLS = {
    "added": "+",
    "modified": "M",
    "deleted": "-",
    "permissions_changed": "P",
    "link_changed": "L",
    "type_changed": "T",
}


def report_payload(report: Report) -> dict[str, object]:
    """Machine-readable execution report. Captured output is not redacted."""
    execution = report.execution
    return {
        "session": {
            "id": report.session.id,
            "name": report.session.name,
            "status": report.session.status,
            "workspace": str(report.session.workspace),
        },
        "execution": {
            "id": execution.id,
            "command": execution.command,
            "exit_code": execution.exit_code,
            "termination_reason": execution.termination_reason,
            "duration_seconds": execution.duration_seconds,
            "cwd": execution.cwd,
            "stdout": execution.stdout,
            "stderr": execution.stderr,
            "backend": report.backend,
        },
        "changes": [
            {"path": change.path, "category": change.category} for change in report.changes
        ],
        "summary": _counts(report.changes),
        "incomplete": report.incomplete,
        "outside_links": report.outside_links,
        "capabilities": report.capabilities,
        "security_notice": report.security_notice,
    }


def render_report(report: Report, *, quiet: bool, verbose: bool) -> str:
    """Plain-text summary. Quiet mode returns an empty string."""
    if quiet:
        return ""
    execution = report.execution
    command = " ".join(execution.command) if execution.command else "(shell)"
    lines = [
        f"Sandbox: {report.session.name}",
        "",
        f"Execution: {command}",
        f"Backend: {report.backend}",
        f"Exit code: {execution.exit_code}",
        f"Termination: {execution.termination_reason}",
        "",
        "Filesystem changes:",
    ]
    if report.incomplete and not report.changes:
        lines.append("  change report incomplete")
    elif not report.changes:
        lines.append("  (none)")
    else:
        for change in report.changes:
            symbol = _SYMBOLS.get(change.category, "?")
            lines.append(f"  {symbol} {change.path}")
    summary = _counts(report.changes)
    lines.extend(
        [
            "",
            "Summary:",
            f"  Added:     {summary['added']}",
            f"  Modified:  {summary['modified']}",
            f"  Deleted:   {summary['deleted']}",
        ]
    )
    if report.outside_links:
        lines.append("")
        lines.append("Links pointing outside the workspace:")
        for link in report.outside_links:
            lines.append(f"  {link}")
    if verbose and report.diff_text:
        lines.extend(["", report.diff_text])
    lines.extend(["", report.security_notice, ""])
    return "\n".join(lines)


def render_change_set(name: str, change_set: ChangeSet, diff_text: str, *, stat: bool) -> str:
    summary = change_set.counts()
    lines = [f"Sandbox: {name}", "", "Filesystem changes:"]
    if change_set.incomplete and not change_set.changes:
        lines.append("  change report incomplete")
    elif not change_set.changes:
        lines.append("  (none)")
    elif not stat:
        for change in change_set.changes:
            symbol = _SYMBOLS.get(change.category, "?")
            lines.append(f"  {symbol} {change.path}")
    lines.extend(
        [
            "",
            "Summary:",
            f"  Added:     {summary['added']}",
            f"  Modified:  {summary['modified']}",
            f"  Deleted:   {summary['deleted']}",
        ]
    )
    if change_set.incomplete:
        lines.extend(["", "Change report incomplete. Sandbox could not read every file."])
    if diff_text and not stat:
        lines.extend(["", diff_text])
    return "\n".join(lines)


def dumps(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, indent=2)


def _counts(changes: Sequence[Change]) -> dict[str, int]:
    summary = {
        "added": 0,
        "modified": 0,
        "deleted": 0,
        "permissions_changed": 0,
        "link_changed": 0,
        "type_changed": 0,
    }
    for change in changes:
        category = change.category
        if category in summary:
            summary[category] += 1
    return summary

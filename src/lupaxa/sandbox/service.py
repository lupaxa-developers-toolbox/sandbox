"""Stage a workspace, run one command, and keep the result."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lupaxa.sandbox.exceptions import SessionBusy, StagingError, UsageError
from lupaxa.sandbox.execution.base import LOCAL_CAPABILITIES
from lupaxa.sandbox.execution.policy import IsolationRequest
from lupaxa.sandbox.execution.registry import open_backend
from lupaxa.sandbox.sessions.store import ExecutionRecord, Session, SessionStore, new_id
from lupaxa.sandbox.workspace.changes import Change, ChangeSet, compare_manifests
from lupaxa.sandbox.workspace.manifest import Manifest, build_manifest
from lupaxa.sandbox.workspace.patterns import DEFAULT_STAGING_EXCLUDES, DEFAULT_TRACKING_EXCLUDES
from lupaxa.sandbox.workspace.staging import stage_copy
from lupaxa.sandbox.workspace.textdiff import file_diff

SECURITY_NOTICE = (
    "Workspace mode is not a security boundary. The command ran with your user "
    "permissions and can read, write, and reach the network outside the workspace."
)

_MAX_FILES = 100_000
_MAX_SIZE_MB = 2048
_GRACE_SECONDS = 5


@dataclass
class Report:
    """The result of one command in a retained session."""

    session: Session
    execution: ExecutionRecord
    changes: list[Change]
    incomplete: bool
    security_notice: str
    outside_links: list[str]
    capabilities: dict[str, bool]
    diff_text: str
    backend: str = "local"


def create_session(
    *,
    source: Path,
    name: str,
    data_dir: Path,
    exclude: list[str] | None = None,
    max_files: int = _MAX_FILES,
    max_size_mb: int = _MAX_SIZE_MB,
    strategy: str = "copy",
    copy_from: Path | None = None,
) -> Session:
    """Copy ``source`` into a new ready session, or leave the workspace empty.

    ``copy_from`` stages that tree while ``source`` stays the recorded project.
    Comparison uses one snapshot for both sessions and still applies back to
    the original project.
    """
    source = Path(source).resolve()
    tree = source if copy_from is None else Path(copy_from).resolve()
    if not source.is_dir():
        raise StagingError(f"Source is not a directory: {source}")
    if not tree.is_dir():
        raise StagingError(f"Source is not a directory: {tree}")
    if strategy not in {"copy", "empty"}:
        raise UsageError(f"Unsupported workspace strategy: {strategy}")
    store = SessionStore(data_dir)
    patterns = list(DEFAULT_STAGING_EXCLUDES if exclude is None else exclude)
    session = store.create(name, source)
    try:
        if strategy == "empty":
            session.workspace.mkdir(parents=True, exist_ok=True)
            empty = Manifest({}, False, [])
            baseline = build_manifest(session.workspace, exclude=[])
            store.save_baseline(session, baseline, [], empty)
        else:
            staged = stage_copy(
                tree,
                session.workspace,
                exclude=patterns,
                max_files=max_files,
                max_size_mb=max_size_mb,
            )
            source_manifest = build_manifest(tree, exclude=patterns)
            baseline = build_manifest(session.workspace, exclude=[])
            store.save_baseline(session, baseline, staged.outside_links, source_manifest)
    except Exception:
        store.destroy(name, force=True)
        raise
    return store.get(name)


def exec_session(
    *,
    name: str,
    command: list[str],
    data_dir: Path,
    timeout_seconds: float,
    shell: bool = False,
    cwd: str | None = None,
    stream: bool = False,
    stdin_path: Path | None = None,
    env: dict[str, str] | None = None,
    tracking_exclude: list[str] | None = None,
    tracking_enabled: bool = True,
    isolation: IsolationRequest | None = None,
) -> Report:
    """Run ``command`` in an existing session. A second run is refused."""
    if not command and not shell:
        raise UsageError("Pass a command after --")
    store = SessionStore(data_dir)
    session = store.get(name)
    if session.status == "running" and session.pid is not None:
        raise SessionBusy(f"Session {name} is already running a command")
    work = _working_directory(session, cwd)
    request = isolation or IsolationRequest()
    backend = open_backend(request)
    started = _now()
    with store.acquire_run(session):
        try:
            result = _execute(
                backend,
                session,
                store,
                command,
                work,
                timeout_seconds,
                shell,
                stream,
                stdin_path,
                env,
            )
            snapshot = build_manifest(session.workspace, exclude=[])
            baseline = store.baseline(session)
            if tracking_exclude is None:
                excludes = list(DEFAULT_TRACKING_EXCLUDES)
            else:
                excludes = list(tracking_exclude)
            if tracking_enabled:
                change_set = compare_manifests(baseline, snapshot, excludes)
            else:
                change_set = ChangeSet([], False, [])
            record = ExecutionRecord(
                id=new_id(),
                command=list(result.command),
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                termination_reason=result.termination_reason,
                duration_seconds=result.duration_seconds,
                started_at=started,
                ended_at=_now(),
                cwd=_relative_cwd(session.workspace, work),
            )
            store.add_execution(session, record, change_set, snapshot)
            store.set_status(session, _status(result.termination_reason, result.exit_code), None)
            fresh = store.get(name)
            return Report(
                session=fresh,
                execution=record,
                changes=change_set.changes,
                incomplete=change_set.incomplete,
                security_notice=backend.notice(request),
                outside_links=fresh.outside_links,
                capabilities=backend.applied(request),
                diff_text=diff_text(fresh, baseline, change_set),
                backend=backend.name,
            )
        except Exception:
            if not session.root.exists():
                return _abandoned(session, command, started)
            raise


def run_once(
    *,
    source: Path,
    name: str,
    command: list[str],
    data_dir: Path,
    timeout_seconds: float,
    shell: bool = False,
    stream: bool = False,
    isolation: IsolationRequest | None = None,
) -> Report:
    """Create a retained session and run one command in it."""
    create_session(source=source, name=name, data_dir=data_dir)
    return exec_session(
        name=name,
        command=command,
        data_dir=data_dir,
        timeout_seconds=timeout_seconds,
        shell=shell,
        stream=stream,
        isolation=isolation,
    )


def destroy_session(*, name: str, data_dir: Path, force: bool = False) -> None:
    SessionStore(data_dir).destroy(name, force=force)


def _execute(
    backend,
    session,
    store,
    command,
    work,
    timeout_seconds,
    shell,
    stream,
    stdin_path,
    env,
):
    def remember(pid: int) -> None:
        store.set_status(session, "running", pid)

    if shell:
        script = command[0] if len(command) == 1 else " ".join(command)
        return backend.run_shell(
            script=script,
            cwd=work,
            timeout_seconds=timeout_seconds,
            grace_seconds=_GRACE_SECONDS,
            env=env,
            stdin_path=stdin_path,
            stream=stream,
            workspace_root=session.workspace,
            on_pid=remember,
        )
    return backend.run(
        argv=command,
        cwd=work,
        timeout_seconds=timeout_seconds,
        grace_seconds=_GRACE_SECONDS,
        env=env,
        stdin_path=stdin_path,
        stream=stream,
        workspace_root=session.workspace,
        on_pid=remember,
    )


def _working_directory(session: Session, cwd: str | None) -> Path:
    if cwd is None or cwd in {"", "."}:
        return session.workspace
    return session.workspace / cwd


def _relative_cwd(workspace: Path, work: Path) -> str:
    try:
        relative = work.resolve().relative_to(workspace.resolve())
    except ValueError:
        return "."
    text = relative.as_posix()
    return "." if text == "." else text


def _status(reason: str, exit_code: int | None) -> str:
    if reason == "interrupted":
        return "interrupted"
    if reason == "timeout" or exit_code not in (0,):
        return "failed"
    return "ready"


def diff_text(session: Session, baseline: Manifest, change_set: ChangeSet) -> str:
    if change_set.incomplete and not change_set.changes:
        return "Change report incomplete. Sandbox could not read every file."
    blocks: list[str] = []
    for change in change_set.changes:
        if change.category not in {"added", "modified"}:
            continue
        current = session.workspace / change.path
        if not current.is_file() or current.is_symlink():
            continue
        new = current.read_bytes()
        old = b""
        if change.category == "modified":
            previous = _original_bytes(session, baseline, change.path)
            if previous is None:
                blocks.append(f"{change.path}: text diff skipped (original bytes unavailable)")
                continue
            old = previous
        diff = file_diff(change.path, old, new)
        if diff.binary:
            blocks.append(f"{change.path}: binary")
        elif diff.skipped:
            blocks.append(f"{change.path}: diff skipped (file is larger than the diff limit)")
        elif diff.text:
            blocks.append(diff.text)
    return "\n".join(blocks)


def _original_bytes(session: Session, baseline: Manifest, relative: str) -> bytes | None:
    entry = baseline.entries.get(relative)
    source = Path(session.source) / relative
    if entry is None or entry.sha256 is None or not source.is_file() or source.is_symlink():
        return None
    payload = source.read_bytes()
    if hashlib.sha256(payload).hexdigest() != entry.sha256:
        return None
    return payload


def _abandoned(session: Session, command: list[str], started: str) -> Report:
    record = ExecutionRecord(
        id=new_id(),
        command=command,
        exit_code=None,
        stdout="",
        stderr="",
        termination_reason="interrupted",
        duration_seconds=0,
        started_at=started,
        ended_at=_now(),
        cwd=".",
    )
    return Report(
        session=session,
        execution=record,
        changes=[],
        incomplete=True,
        security_notice=SECURITY_NOTICE,
        outside_links=session.outside_links,
        capabilities=dict(LOCAL_CAPABILITIES),
        diff_text="",
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()

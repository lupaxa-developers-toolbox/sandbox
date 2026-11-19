"""The sandbox command."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from typer._click.exceptions import ClickException

from lupaxa.sandbox.compare import compare_recipe, render_comparison
from lupaxa.sandbox.exceptions import SandboxError, UsageError
from lupaxa.sandbox.execution.policy import IsolationRequest
from lupaxa.sandbox.execution.registry import catalog, describe
from lupaxa.sandbox.locations import data_dir
from lupaxa.sandbox.operations.apply import apply_changes
from lupaxa.sandbox.operations.export import export_files
from lupaxa.sandbox.operations.retention import clean_sessions, storage_report
from lupaxa.sandbox.recipes.loader import user_config_path
from lupaxa.sandbox.recipes.runner import (
    process_exit_code,
    run_configured_command,
    run_recipe_file,
    show_config,
    validate_file,
)
from lupaxa.sandbox.reporting import dumps, render_change_set, render_report, report_payload
from lupaxa.sandbox.service import (
    SECURITY_NOTICE,
    create_session,
    destroy_session,
    diff_text,
    exec_session,
    run_once,
)
from lupaxa.sandbox.sessions.store import SessionStore
from lupaxa.sandbox.tui.launch import run_tui
from lupaxa.sandbox.version import __version__
from lupaxa.sandbox.workspace.changes import compare_manifests
from lupaxa.sandbox.workspace.manifest import build_manifest
from lupaxa.sandbox.workspace.patterns import DEFAULT_TRACKING_EXCLUDES

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help=(
        "Run commands in a disposable copy of a project and show what changed.\n\n"
        "Workspace mode is not a security boundary. Commands run with your "
        "permissions and can read, write, and reach the network outside the workspace."
    ),
)
backend_app = typer.Typer(help="Show execution backends and what they actually isolate.")
app.add_typer(backend_app, name="backend")
config_app = typer.Typer(help="Show the user configuration and the resolved defaults.")
app.add_typer(config_app, name="config")

_console = Console(highlight=False)


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the package version and exit.",
            callback=_version,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Run commands in a disposable workspace."""
    del version


@app.command(context_settings={"allow_interspersed_args": False})
def run(
    command: Annotated[
        list[str] | None,
        typer.Argument(help="Command after --."),
    ] = None,
    source: Annotated[
        Path | None,
        typer.Option("--source", help="Directory to copy."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="Session name. Generated if omitted."),
    ] = None,
    timeout: Annotated[
        int | None,
        typer.Option("--timeout", help="Seconds before termination."),
    ] = None,
    shell: Annotated[
        bool,
        typer.Option("--shell", help="Run one command string in a shell."),
    ] = False,
    venv: Annotated[
        bool,
        typer.Option("--venv", help="Create a virtual environment in the workspace."),
    ] = False,
    python: Annotated[
        str | None,
        typer.Option("--python", help="Interpreter to use. Sandbox does not download Python."),
    ] = None,
    backend: Annotated[
        str | None,
        typer.Option("--backend", help="local, restricted, docker, or podman."),
    ] = None,
    network: Annotated[
        str | None,
        typer.Option("--network", help="allow or deny. An unenforceable deny is refused."),
    ] = None,
    memory_mb: Annotated[
        int | None,
        typer.Option("--memory-mb", help="Memory limit. Refused when it cannot be enforced."),
    ] = None,
    cpus: Annotated[
        float | None,
        typer.Option("--cpus", help="CPU limit. Refused when the backend cannot enforce it."),
    ] = None,
    image: Annotated[
        str | None,
        typer.Option("--image", help="Local container image. Sandbox does not pull images."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print a JSON report.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="Print nothing on success.")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Include text diffs.")] = False,
) -> None:
    """Stage the project, run one command or a recipe, and keep the workspace."""
    try:
        argv = list(command or [])
        if _is_recipe(argv):
            if shell:
                raise UsageError("A recipe does not use --shell")
            result = run_recipe_file(
                Path(argv[0]),
                data_dir=data_dir(),
                cli=_cli_overrides(
                    source,
                    name,
                    timeout,
                    venv,
                    python,
                    backend,
                    network,
                    memory_mb,
                    cpus,
                    image,
                ),
                stream=not json_output and not quiet,
            )
            _emit_recipe(result, json_output=json_output, quiet=quiet)
            raise typer.Exit(code=process_exit_code(result))
        argv = _command(command, shell)
        isolation = _isolation(backend, network, memory_mb, cpus, image)
        if venv or python:
            report = run_configured_command(
                source=source or Path("."),
                name=name or _generated_name(),
                command=argv,
                data_dir=data_dir(),
                timeout_seconds=timeout if timeout is not None else 300,
                python=python or sys.executable,
                venv=venv,
                stream=not json_output and not quiet,
                isolation=isolation,
            )
        else:
            report = run_once(
                source=source or Path("."),
                name=name or _generated_name(),
                command=argv,
                data_dir=data_dir(),
                timeout_seconds=timeout if timeout is not None else 300,
                shell=shell,
                stream=not json_output and not quiet,
                isolation=isolation,
            )
        _emit(report, json_output=json_output, quiet=quiet, verbose=verbose)
    except SandboxError as exc:
        _fail(exc)


@app.command()
def create(
    name: Annotated[str, typer.Argument(help="Session name.")],
    source: Annotated[
        Path,
        typer.Option("--source", help="Directory to copy."),
    ] = Path("."),
) -> None:
    """Copy a project into a new session without running a command."""
    try:
        session = create_session(source=source, name=name, data_dir=data_dir())
        typer.echo(f"Created {session.name} at {session.workspace}")
    except SandboxError as exc:
        _fail(exc)


@app.command(context_settings={"allow_interspersed_args": False})
def exec(
    name: Annotated[str, typer.Argument(help="Session name or id.")],
    command: Annotated[
        list[str] | None,
        typer.Argument(help="Command after --."),
    ] = None,
    timeout: Annotated[
        int,
        typer.Option("--timeout", help="Seconds before termination."),
    ] = 300,
    cwd: Annotated[
        str | None,
        typer.Option("--cwd", help="Workspace-relative working directory."),
    ] = None,
    shell: Annotated[
        bool,
        typer.Option("--shell", help="Run one command string in a shell."),
    ] = False,
    backend: Annotated[str | None, typer.Option("--backend", help="Execution backend.")] = None,
    network: Annotated[str | None, typer.Option("--network", help="allow or deny.")] = None,
    memory_mb: Annotated[int | None, typer.Option("--memory-mb", help="Memory limit.")] = None,
    cpus: Annotated[float | None, typer.Option("--cpus", help="CPU limit.")] = None,
    image: Annotated[str | None, typer.Option("--image", help="Local container image.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print a JSON report.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="Print nothing on success.")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Include text diffs.")] = False,
) -> None:
    """Run a command in an existing session."""
    try:
        report = exec_session(
            name=name,
            command=_command(command, shell),
            data_dir=data_dir(),
            timeout_seconds=timeout,
            shell=shell,
            cwd=cwd,
            stream=not json_output and not quiet,
            isolation=_isolation(backend, network, memory_mb, cpus, image),
        )
        _emit(report, json_output=json_output, quiet=quiet, verbose=verbose)
    except SandboxError as exc:
        _fail(exc)


@app.command("list")
def list_sessions(
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List retained sessions."""
    try:
        sessions = SessionStore(data_dir()).list_sessions()
        if json_output:
            typer.echo(
                dumps(
                    {
                        "sessions": [
                            {
                                "name": item.name,
                                "id": item.id,
                                "status": item.status,
                            }
                            for item in sessions
                        ]
                    }
                )
            )
            return
        if not sessions:
            typer.echo("No sessions.")
            return
        for item in sessions:
            typer.echo(f"{item.name}  {item.status}  {item.id}")
    except SandboxError as exc:
        _fail(exc)


@app.command()
def show(
    name: Annotated[str, typer.Argument(help="Session name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Show a session, its last command, and where the workspace is."""
    try:
        session = SessionStore(data_dir()).get(name)
    except SandboxError as exc:
        _fail(exc)
        return
    payload = {
        "name": session.name,
        "id": session.id,
        "status": session.status,
        "source": session.source,
        "workspace": str(session.workspace),
        "outside_links": session.outside_links,
        "executions": [
            {
                "id": item.id,
                "command": item.command,
                "exit_code": item.exit_code,
                "termination_reason": item.termination_reason,
            }
            for item in session.executions
        ],
        "security_notice": SECURITY_NOTICE,
    }
    if json_output:
        typer.echo(dumps(payload))
        return
    typer.echo(f"Sandbox: {session.name}")
    typer.echo(f"Status: {session.status}")
    typer.echo(f"Workspace: {session.workspace}")
    typer.echo(f"Executions: {len(session.executions)}")
    typer.echo("")
    typer.echo(SECURITY_NOTICE)


@app.command()
def diff(
    name: Annotated[str, typer.Argument(help="Session name.")],
    stat: Annotated[bool, typer.Option("--stat", help="Show counts only.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Show filesystem changes since the session was created."""
    try:
        store = SessionStore(data_dir())
        session = store.get(name)
        baseline = store.baseline(session)
        current = build_manifest(session.workspace, exclude=[])
        change_set = compare_manifests(baseline, current, DEFAULT_TRACKING_EXCLUDES)
    except SandboxError as exc:
        _fail(exc)
        return
    text = "" if stat else diff_text(session, baseline, change_set)
    if json_output:
        typer.echo(
            dumps(
                {
                    "summary": change_set.counts(),
                    "incomplete": change_set.incomplete,
                    "changes": [
                        {"path": change.path, "category": change.category}
                        for change in change_set.changes
                    ],
                    "diff": text,
                }
            )
        )
        return
    rendered = render_change_set(name, change_set, text, stat=stat)
    _console.print(rendered, markup=False, highlight=False)


@app.command()
def logs(
    name: Annotated[str, typer.Argument(help="Session name.")],
) -> None:
    """Print captured stdout and stderr from the latest command."""
    try:
        session = SessionStore(data_dir()).get(name)
    except SandboxError as exc:
        _fail(exc)
        return
    if not session.executions:
        typer.echo("No executions.")
        return
    latest = session.executions[-1]
    typer.echo(latest.stdout, nl=False)
    if latest.stderr:
        typer.echo(latest.stderr, nl=False, err=True)


@app.command()
def history(
    name: Annotated[str, typer.Argument(help="Session name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List commands that have run in a session."""
    try:
        session = SessionStore(data_dir()).get(name)
    except SandboxError as exc:
        _fail(exc)
        return
    rows = [
        {
            "id": item.id,
            "command": item.command,
            "exit_code": item.exit_code,
            "termination_reason": item.termination_reason,
            "duration_seconds": item.duration_seconds,
        }
        for item in session.executions
    ]
    if json_output:
        typer.echo(dumps({"executions": rows}))
        return
    if not rows:
        typer.echo("No executions.")
        return
    for index, item in enumerate(rows, start=1):
        command = " ".join(item["command"]) if isinstance(item["command"], list) else ""
        typer.echo(f"{index}  exit {item['exit_code']}  {item['termination_reason']}  {command}")


@app.command()
def destroy(
    name: Annotated[str, typer.Argument(help="Session name.")],
    force: Annotated[
        bool,
        typer.Option("--force", help="Stop a running command, then delete."),
    ] = False,
) -> None:
    """Delete a session and its workspace."""
    try:
        destroy_session(name=name, data_dir=data_dir(), force=force)
        typer.echo(f"Destroyed {name}")
    except SandboxError as exc:
        _fail(exc)


@app.command()
def export(
    name: Annotated[str, typer.Argument(help="Session name.")],
    include: Annotated[
        list[str] | None,
        typer.Option("--include", help="File pattern to copy."),
    ] = None,
    to: Annotated[Path | None, typer.Option("--to", help="Directory to copy into.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show the plan only.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Overwrite existing files.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Overwrite newer files too.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Copy selected workspace files. Files stay in the workspace."""
    try:
        if to is None:
            raise UsageError("Pass --to with a destination directory")
        result = export_files(
            name=name,
            data_dir=data_dir(),
            include=list(include or []),
            destination=to,
            dry_run=dry_run,
            yes=yes,
            force=force,
        )
    except SandboxError as exc:
        _fail(exc)
        return
    payload = {
        "dry_run": result.dry_run,
        "destination": str(result.destination),
        "files": [
            {"path": item.path, "action": item.action, "reason": item.reason}
            for item in result.items
        ],
    }
    if json_output:
        typer.echo(dumps(payload))
        return
    for item in result.items:
        suffix = f" ({item.reason})" if item.reason else ""
        typer.echo(f"{item.action}  {item.path}{suffix}")


@app.command()
def apply(
    name: Annotated[str, typer.Argument(help="Session name.")],
    include: Annotated[
        list[str] | None,
        typer.Option("--include", help="Limit the change set."),
    ] = None,
    select_all: Annotated[
        bool,
        typer.Option("--all", help="Select every eligible change."),
    ] = False,
    allow_delete: Annotated[bool, typer.Option("--delete", help="Include file deletions.")] = False,
    allow_links: Annotated[
        bool,
        typer.Option("--replace-links", help="Include link and type changes."),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show the plan only.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Apply workspace changes that still match the original files."""
    try:
        result = apply_changes(
            name=name,
            data_dir=data_dir(),
            include=list(include) if include else None,
            select_all=select_all,
            allow_delete=allow_delete,
            allow_links=allow_links,
            dry_run=dry_run,
        )
    except SandboxError as exc:
        _fail(exc)
        return
    payload = {
        "dry_run": result.dry_run,
        "partial": result.partial,
        "journal": str(result.journal) if result.journal else "",
        "atomic": False,
        "files": [
            {
                "path": item.path,
                "action": item.action,
                "reason": item.reason,
                "pre_hash": item.pre_hash or "",
                "post_hash": item.post_hash or "",
            }
            for item in result.items
        ],
    }
    if json_output:
        typer.echo(dumps(payload))
    else:
        for item in result.items:
            suffix = f" ({item.reason})" if item.reason else ""
            typer.echo(f"{item.action}  {item.path}{suffix}")
        if result.journal is not None:
            typer.echo(f"Journal: {result.journal}")
            typer.echo("This apply is not atomic. The journal can restore a partial write.")
        if result.error:
            typer.echo(result.error, err=True)
    if result.partial:
        raise typer.Exit(code=1)
    if result.conflicts:
        raise typer.Exit(code=4)


@app.command()
def clean(
    older_than: Annotated[
        str | None,
        typer.Option("--older-than", help="Remove sessions older than this, such as 14d."),
    ] = None,
    failed: Annotated[
        bool,
        typer.Option("--failed", help="Remove sessions whose last run failed."),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be removed.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Remove old, failed, or incomplete sessions. Running sessions stay."""
    try:
        result = clean_sessions(
            data_dir=data_dir(),
            older_than=older_than,
            failed=failed,
            dry_run=dry_run,
        )
    except SandboxError as exc:
        _fail(exc)
        return
    payload = {
        "dry_run": result.dry_run,
        "removed": [{"name": item.name, "reason": item.reason} for item in result.removed],
        "skipped_running": result.skipped_running,
    }
    if json_output:
        typer.echo(dumps(payload))
        return
    verb = "Would remove" if result.dry_run else "Removed"
    if not result.removed:
        typer.echo(f"{verb} 0 sessions.")
    else:
        typer.echo(f"{verb} {len(result.removed)} sessions.")
        for item in result.removed:
            typer.echo(f"  {item.name}  {item.reason}")
    if result.skipped_running:
        typer.echo(f"Skipped {len(result.skipped_running)} running sessions.")


@app.command()
def storage(
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Show how much disk retained sessions are using."""
    try:
        report = storage_report(data_dir())
    except SandboxError as exc:
        _fail(exc)
        return
    payload = {
        "session_count": report.session_count,
        "total_bytes": report.total_bytes,
        "largest": [
            {"name": item.name, "status": item.status, "size_bytes": item.size_bytes}
            for item in report.largest
        ],
    }
    if json_output:
        typer.echo(dumps(payload))
        return
    typer.echo(f"Sessions: {report.session_count}")
    typer.echo(f"Total: {_size(report.total_bytes)}")
    if report.largest:
        typer.echo("Largest:")
        for item in report.largest:
            typer.echo(f"  {item.name}  {_size(item.size_bytes)}  {item.status}")


def _size(count: int) -> str:
    if count < 1024:
        return f"{count} B"
    if count < 1024 * 1024:
        return f"{count / 1024:.1f} KiB"
    return f"{count / (1024 * 1024):.1f} MiB"


@app.command()
def doctor() -> None:
    """Check the data directory and which backends answer a probe."""
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write-probe"
    try:
        probe.write_text("ok")
        probe.unlink()
        writable = True
    except OSError as exc:
        writable = False
        typer.echo(f"Data directory is not writable: {exc}", err=True)
    typer.echo(f"Data directory: {path}")
    typer.echo(f"Writable: {writable}")
    typer.echo(f"Python: {sys.version.split()[0]}")
    for status in catalog():
        state = "available" if status.available else "unavailable"
        typer.echo(f"Backend {status.name}: {state} ({status.reason})")
    typer.echo("")
    typer.echo(SECURITY_NOTICE)
    if not writable:
        raise typer.Exit(code=1)


@backend_app.command("list")
def backend_list() -> None:
    """List backends and whether a probe says they are available."""
    for status in catalog():
        state = "available" if status.available else "unavailable"
        typer.echo(f"{status.name}  {state}")
        for key, enabled in status.capabilities.items():
            typer.echo(f"  {key}: {enabled}")
        typer.echo(f"  {status.threat_model}")
        typer.echo("")


@backend_app.command("info")
def backend_info(name: Annotated[str, typer.Argument(help="Backend name.")]) -> None:
    """Show one backend's probe result and threat model."""
    try:
        status = describe(name)
    except SandboxError as exc:
        _fail(exc)
        return
    state = "available" if status.available else "unavailable"
    typer.echo(f"{status.name}  {state}")
    typer.echo(status.reason)
    for key, enabled in status.capabilities.items():
        typer.echo(f"  {key}: {enabled}")
    typer.echo("")
    typer.echo(status.threat_model)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    try:
        result = app(args=argv, standalone_mode=False)
    except typer.Exit as exc:
        return int(exc.exit_code or 0)
    except ClickException as exc:
        hint = _flag_hint(exc)
        if hint:
            exc.message = f"{exc.message}. {hint}"
        exc.show()
        code = getattr(exc, "exit_code", 2)
        return int(code) if isinstance(code, int) else 2
    except SandboxError as exc:
        typer.echo(str(exc), err=True)
        return exc.exit_code
    if isinstance(result, int):
        return result
    return 0


def _flag_hint(exc: ClickException) -> str:
    """Point a mistaken ``--command`` at the real subcommand."""
    option = getattr(exc, "option_name", None)
    if not isinstance(option, str) or not option.startswith("-"):
        return ""
    name = option.lstrip("-")
    if name in _command_names():
        return f"Did you mean 'sandbox {name}'?"
    return ""


def _command_names() -> set[str]:
    names: set[str] = set()
    for command in app.registered_commands:
        if command.name:
            names.add(command.name)
        elif command.callback is not None:
            names.add(command.callback.__name__.replace("_", "-"))
    for group in app.registered_groups:
        if group.name:
            names.add(group.name)
    return names


@app.command()
def compare(
    recipe: Annotated[Path, typer.Argument(help="Recipe file.")],
    python_a: Annotated[
        str | None,
        typer.Option("--python-a", help="Interpreter for environment A."),
    ] = None,
    python_b: Annotated[
        str | None,
        typer.Option("--python-b", help="Interpreter for environment B."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="Print nothing on success.")] = False,
) -> None:
    """Run a recipe twice from one snapshot and report the differences."""
    try:
        report = compare_recipe(
            recipe,
            data_dir=data_dir(),
            python_a=python_a,
            python_b=python_b,
            stream=not json_output and not quiet,
        )
    except SandboxError as exc:
        _fail(exc)
        return
    if json_output:
        typer.echo(dumps(report.to_dict()))
    elif not quiet:
        typer.echo(render_comparison(report))
    if report.timed_out:
        raise typer.Exit(code=5)


@app.command()
def tui() -> None:
    """Browse sessions, diffs, and export. Requires the optional tui install."""
    try:
        run_tui(data_dir())
    except SandboxError as exc:
        _fail(exc)


@app.command()
def validate(
    path: Annotated[Path, typer.Argument(help="Recipe file.")] = Path("sandbox.yml"),
) -> None:
    """Check a recipe before creating a workspace."""
    try:
        validate_file(path)
    except SandboxError as exc:
        _fail(exc)
    typer.echo(f"Valid: {path}")


@config_app.command("show")
def config_show(
    resolved: Annotated[
        bool,
        typer.Option("--resolved", help="Show defaults merged with the user file."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Show configuration. Resolved output redacts sensitive environment values."""
    try:
        payload = show_config(resolved=resolved)
    except SandboxError as exc:
        _fail(exc)
        return
    if json_output:
        typer.echo(dumps(payload))
        return
    typer.echo(dumps(payload))


@config_app.command("path")
def config_path() -> None:
    """Print the user configuration file path."""
    typer.echo(str(user_config_path()))


def _is_recipe(argv: list[str]) -> bool:
    if len(argv) != 1:
        return False
    return Path(argv[0]).suffix.lower() in {".yml", ".yaml"}


def _isolation(
    backend: str | None,
    network: str | None,
    memory_mb: int | None,
    cpus: float | None,
    image: str | None,
) -> IsolationRequest:
    return IsolationRequest(
        backend=backend or "local",
        network=network or "allow",
        memory_mb=memory_mb,
        cpus=cpus,
        image=image,
    )


def _cli_overrides(
    source: Path | None,
    name: str | None,
    timeout: int | None,
    venv: bool,
    python: str | None,
    backend: str | None = None,
    network: str | None = None,
    memory_mb: int | None = None,
    cpus: float | None = None,
    image: str | None = None,
) -> dict[str, object]:
    cli: dict[str, object] = {}
    if source is not None:
        cli["workspace"] = {"source": str(source.resolve())}
    if name is not None:
        cli["sandbox"] = {"name": name}
    execution: dict[str, object] = {}
    if timeout is not None:
        execution["timeout_seconds"] = timeout
    if backend is not None:
        execution["backend"] = backend
    if network is not None:
        execution["network"] = network
    if image is not None:
        execution["image"] = image
    resources: dict[str, object] = {}
    if memory_mb is not None:
        resources["memory_mb"] = memory_mb
    if cpus is not None:
        resources["cpus"] = cpus
    if resources:
        execution["resources"] = resources
    if execution:
        cli["execution"] = execution
    environment: dict[str, object] = {}
    if venv:
        environment["type"] = "venv"
    if python is not None:
        environment["python"] = python
    if environment:
        cli["environment"] = environment
    return cli


def _emit_recipe(result, *, json_output: bool, quiet: bool) -> None:
    payload = {
        "session": result.session_name,
        "destroyed": result.destroyed,
        "environment": {"type": result.environment_type, "secure": False},
        "commands": [
            {"id": item.id, "status": item.status, "exit_code": item.exit_code}
            for item in result.outcomes
        ],
        "security_notice": result.security_notice,
        "venv_notice": result.venv_notice,
    }
    if json_output:
        typer.echo(dumps(payload))
        return
    if quiet:
        return
    typer.echo(f"Sandbox: {result.session_name}")
    for item in result.outcomes:
        typer.echo(f"{item.status}  {item.id}  exit {item.exit_code}")
    typer.echo("")
    typer.echo(result.security_notice)
    if result.venv_notice:
        typer.echo(result.venv_notice)


def _command(command: list[str] | None, shell: bool) -> list[str]:
    argv = list(command or [])
    if not argv:
        raise UsageError("Pass a command after --")
    if shell and len(argv) != 1:
        raise UsageError("With --shell, pass one quoted command string after --")
    return argv


def _generated_name() -> str:
    return "run-" + uuid.uuid4().hex[:8]


def _emit(report, *, json_output: bool, quiet: bool, verbose: bool) -> None:
    if json_output:
        typer.echo(dumps(report_payload(report)))
    else:
        text = render_report(report, quiet=quiet, verbose=verbose)
        if text:
            _console.print(text, markup=False, highlight=False)
    raise typer.Exit(code=_exit_code(report))


def _exit_code(report) -> int:
    reason = report.execution.termination_reason
    if reason == "timeout":
        return 5
    if reason == "interrupted":
        return 6
    code = report.execution.exit_code
    if isinstance(code, int) and 0 <= code <= 255:
        return code
    return 1


def _fail(exc: SandboxError) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(code=exc.exit_code)


if __name__ == "__main__":
    sys.exit(main())

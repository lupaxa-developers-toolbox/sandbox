"""Run a validated recipe. The workspace is created only after validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lupaxa.sandbox.exceptions import NotFoundError, UsageError
from lupaxa.sandbox.execution.environment import (
    VENV_NOTICE,
    command_environment,
    create_venv,
    ensure_interpreter,
    install_packages,
    venv_python,
    write_environment_metadata,
)
from lupaxa.sandbox.execution.policy import IsolationRequest
from lupaxa.sandbox.recipes.loader import (
    load_mapping,
    resolve_recipe,
    resolved_document,
    user_config_path,
)
from lupaxa.sandbox.recipes.models import Recipe
from lupaxa.sandbox.service import (
    SECURITY_NOTICE,
    Report,
    create_session,
    destroy_session,
    exec_session,
)
from lupaxa.sandbox.sessions.store import SessionStore


@dataclass
class CommandOutcome:
    """One recipe command after the run."""

    id: str
    status: str
    exit_code: int | None


@dataclass
class RecipeRun:
    """The session and every command the recipe asked for."""

    session_name: str
    destroyed: bool
    outcomes: list[CommandOutcome]
    environment_type: str
    security_notice: str
    venv_notice: str


def validate_file(path: Path) -> Recipe:
    """Load a recipe and check the source path and interpreter before any copy."""
    recipe_path = Path(path)
    payload = load_mapping(recipe_path)
    _require_commands(payload)
    recipe = resolve_recipe(recipe=payload)
    _check_ready(recipe, recipe_path.parent)
    return recipe


def prepare_recipe(
    path: Path,
    cli: dict[str, object] | None = None,
) -> tuple[Recipe, Path]:
    """Validate a recipe and return it with the source directory to record."""
    recipe_path = Path(path)
    payload = load_mapping(recipe_path)
    _require_commands(payload)
    recipe = resolve_recipe(user=_user_mapping(), recipe=payload, cli=cli)
    source = _check_ready(recipe, recipe_path.parent)
    return recipe, source


def run_resolved_recipe(
    recipe: Recipe,
    source: Path,
    data_dir: Path,
    *,
    stream: bool = False,
    copy_from: Path | None = None,
) -> RecipeRun:
    """Run an already validated recipe, optionally staging from ``copy_from``."""
    return _execute(recipe, source, data_dir, stream, copy_from)


def run_recipe_file(
    path: Path,
    *,
    data_dir: Path,
    cli: dict[str, object] | None = None,
    stream: bool = False,
) -> RecipeRun:
    """Validate ``path``, then stage, prepare the environment, and run its commands."""
    recipe, source = prepare_recipe(path, cli)
    return _execute(recipe, source, data_dir, stream, None)


def show_config(*, resolved: bool) -> dict[str, object]:
    """Return the user file, or the redacted defaults merged with that file."""
    path = user_config_path()
    if not path.is_file():
        recipe = resolve_recipe()
        if resolved:
            return resolved_document(recipe)
        return resolved_document(recipe)
    payload = load_mapping(path)
    if not resolved:
        return payload
    return resolved_document(resolve_recipe(user=payload))


def _execute(
    recipe: Recipe,
    source: Path,
    data_dir: Path,
    stream: bool,
    copy_from: Path | None,
) -> RecipeRun:
    name = recipe.sandbox.name or _generated_name()
    session = create_session(
        source=source,
        name=name,
        data_dir=data_dir,
        exclude=list(recipe.workspace.staging.exclude),
        max_files=recipe.workspace.limits.max_files,
        max_size_mb=recipe.workspace.limits.max_size_mb,
        strategy=recipe.workspace.strategy,
        copy_from=copy_from,
    )
    _write_effective(session.root, recipe)
    outcomes: list[CommandOutcome] = []
    failed = False
    try:
        path_prefix, virtual_env = _prepare_environment(session.workspace, recipe)
        env = command_environment(
            inherit=recipe.execution.environment.inherit,
            values=dict(recipe.execution.environment.set),
            unset=list(recipe.execution.environment.unset),
            path_prefix=path_prefix,
            virtual_env=virtual_env,
        )
        excludes = list(recipe.tracking.exclude)
        if recipe.environment.type == "venv" and ".venv/" not in excludes:
            excludes.append(".venv/")
        isolation = IsolationRequest(
            backend=recipe.execution.backend,
            network=recipe.execution.network,
            memory_mb=recipe.execution.resources.memory_mb,
            cpus=recipe.execution.resources.cpus,
            image=recipe.execution.image,
        )
        for command in recipe.execution.commands:
            if failed and recipe.execution.stop_on_error:
                outcomes.append(CommandOutcome(command.id, "skipped", None))
                continue
            report = exec_session(
                name=session.name,
                command=list(command.run),
                data_dir=data_dir,
                timeout_seconds=recipe.execution.timeout_seconds,
                stream=stream,
                env=env,
                tracking_exclude=excludes,
                tracking_enabled=recipe.tracking.enabled,
                isolation=isolation,
            )
            status = _command_status(
                report.execution.termination_reason,
                report.execution.exit_code,
            )
            outcomes.append(CommandOutcome(command.id, status, report.execution.exit_code))
            if status != "completed":
                failed = True
    except Exception:
        _mark_failed(data_dir, session.name)
        raise
    if failed:
        _mark_failed(data_dir, session.name)
    destroyed = _cleanup(recipe, data_dir, session.name, failed)
    notice = VENV_NOTICE if recipe.environment.type in {"venv", "existing"} else ""
    return RecipeRun(
        session.name,
        destroyed,
        outcomes,
        recipe.environment.type,
        SECURITY_NOTICE,
        notice,
    )


def _prepare_environment(workspace: Path, recipe: Recipe) -> tuple[Path | None, Path | None]:
    """Return ``(PATH prefix, VIRTUAL_ENV directory)``."""
    if recipe.environment.type == "none":
        write_environment_metadata(
            workspace,
            {"type": "none", "secure": False, "note": SECURITY_NOTICE},
        )
        return None, None
    interpreter = ensure_interpreter(recipe.environment.python or "")
    if recipe.environment.type == "existing":
        write_environment_metadata(
            workspace,
            {
                "type": "existing",
                "python": str(interpreter),
                "secure": False,
                "note": VENV_NOTICE,
            },
        )
        return interpreter.parent, None
    venv_dir = create_venv(interpreter, workspace)
    install_packages(venv_python(venv_dir), workspace, list(recipe.environment.install))
    write_environment_metadata(
        workspace,
        {
            "type": "venv",
            "python": str(interpreter),
            "prefix": str(venv_dir),
            "install": list(recipe.environment.install),
            "secure": False,
            "note": VENV_NOTICE,
        },
    )
    return venv_python(venv_dir).parent, venv_dir


def _check_ready(recipe: Recipe, base: Path) -> Path:
    source = _resolve_source(recipe.workspace.source, base)
    if not source.is_dir():
        raise UsageError(f"Source is not a directory: {source}")
    if recipe.environment.type in {"venv", "existing"}:
        ensure_interpreter(recipe.environment.python or "")
    return source


def _resolve_source(source: str, base: Path) -> Path:
    path = Path(source).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _require_commands(payload: dict[str, object]) -> None:
    execution = payload.get("execution")
    commands = execution.get("commands") if isinstance(execution, dict) else None
    if not commands:
        raise UsageError("Recipe has no commands")


def _user_mapping() -> dict[str, object] | None:
    path = user_config_path()
    if not path.is_file():
        return None
    return load_mapping(path)


def _write_effective(root: Path, recipe: Recipe) -> None:
    path = root / "effective-config.json"
    path.write_text(json.dumps(resolved_document(recipe), indent=2) + "\n")


def _command_status(reason: str, exit_code: int | None) -> str:
    if reason == "timeout":
        return "timeout"
    if reason == "completed" and exit_code == 0:
        return "completed"
    return "failed"


def _cleanup(recipe: Recipe, data_dir: Path, name: str, failed: bool) -> bool:
    policy = recipe.cleanup.on_failure if failed else recipe.cleanup.on_success
    if policy != "destroy":
        return False
    destroy_session(name=name, data_dir=data_dir)
    return True


def _mark_failed(data_dir: Path, name: str) -> None:
    store = SessionStore(data_dir)
    try:
        session = store.get(name)
    except NotFoundError:
        return
    store.set_status(session, "failed", None)


def _generated_name() -> str:
    import uuid

    return "run-" + uuid.uuid4().hex[:8]


def run_configured_command(
    *,
    source: Path,
    name: str,
    command: list[str],
    data_dir: Path,
    timeout_seconds: float,
    python: str,
    venv: bool,
    stream: bool,
    isolation: IsolationRequest | None = None,
) -> Report:
    """Run one command with a selected interpreter or a new virtual environment."""
    interpreter = ensure_interpreter(python)
    session = create_session(source=source, name=name, data_dir=data_dir)
    try:
        if venv:
            venv_dir = create_venv(interpreter, session.workspace)
            prefix = venv_python(venv_dir).parent
            virtual: Path | None = venv_dir
            note = VENV_NOTICE
            kind = "venv"
        else:
            prefix = interpreter.parent
            virtual = None
            note = VENV_NOTICE
            kind = "existing"
        write_environment_metadata(
            session.workspace,
            {"type": kind, "python": str(interpreter), "secure": False, "note": note},
        )
        env = command_environment(
            inherit=True,
            values={},
            unset=[],
            path_prefix=prefix,
            virtual_env=virtual,
        )
        return exec_session(
            name=session.name,
            command=command,
            data_dir=data_dir,
            timeout_seconds=timeout_seconds,
            stream=stream,
            env=env,
            isolation=isolation,
        )
    except Exception:
        _mark_failed(data_dir, session.name)
        raise


def process_exit_code(run: RecipeRun) -> int:
    """Return the first failed command's code, or 5 when a command times out."""
    for outcome in run.outcomes:
        if outcome.status == "timeout":
            return 5
        if outcome.status == "failed":
            code = outcome.exit_code
            if isinstance(code, int) and 0 <= code <= 255:
                return code
            return 1
    return 0

"""Optional virtual environments. They are not a security boundary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from lupaxa.sandbox.exceptions import StagingError, UsageError

VENV_NOTICE = (
    "A virtual environment is not a security boundary. It does not make the "
    "command reproducible unless the recipe pins package versions."
)


def ensure_interpreter(name: str) -> Path:
    """Find a local interpreter. Sandbox does not download Python."""
    candidate = Path(name).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate.resolve()
    found = shutil.which(name)
    if found is None:
        raise UsageError(f"Python interpreter not found: {name}. Sandbox does not download Python.")
    return Path(found).resolve()


def create_venv(interpreter: Path, workspace: Path) -> Path:
    """Create ``workspace/.venv`` with the standard library ``venv`` module."""
    venv_dir = workspace / ".venv"
    completed = subprocess.run(
        [str(interpreter), "-m", "venv", str(venv_dir)],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "venv failed"
        raise StagingError(f"Could not create a virtual environment: {detail}")
    return venv_dir


def install_packages(python: Path, workspace: Path, packages: list[str]) -> None:
    """Install each requirement with the environment's pip. Network access is possible."""
    import shlex

    for package in packages:
        args = shlex.split(package)
        if not args:
            raise UsageError("An install entry is empty")
        completed = subprocess.run(
            [str(python), "-m", "pip", "install", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            lines = completed.stderr.strip().splitlines()
            detail = lines[-1] if lines else package
            raise StagingError(f"Dependency install failed ({package}): {detail}")


def venv_python(venv_dir: Path) -> Path:
    folder = "Scripts" if os.name == "nt" else "bin"
    name = "python.exe" if os.name == "nt" else "python"
    return venv_dir / folder / name


def command_environment(
    *,
    inherit: bool,
    values: dict[str, str],
    unset: list[str],
    path_prefix: Path | None,
    virtual_env: Path | None,
) -> dict[str, str]:
    """Build the process environment, with the selected binaries first on PATH."""
    env = dict(os.environ) if inherit else {}
    for name in unset:
        env.pop(name, None)
    env.update(values)
    if path_prefix is not None:
        env["PATH"] = str(path_prefix) + os.pathsep + env.get("PATH", "")
    if virtual_env is not None:
        env["VIRTUAL_ENV"] = str(virtual_env)
    return env


def write_environment_metadata(workspace: Path, payload: dict[str, object]) -> None:
    """Record how the environment was created. ``secure`` is always false."""
    path = workspace.parent.parent / "environment.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")

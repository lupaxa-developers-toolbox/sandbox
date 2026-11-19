"""Recipes are validated before a workspace exists, then run in order."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from lupaxa.sandbox.exceptions import NotFoundError, UsageError
from lupaxa.sandbox.recipes.loader import resolve_recipe
from lupaxa.sandbox.recipes.runner import run_recipe_file, validate_file
from lupaxa.sandbox.sessions.store import SessionStore


def _write(path: Path, text: str) -> None:
    path.write_text(text)


def _recipe(tmp_path: Path, body: str, name: str = "sandbox.yml") -> Path:
    source = tmp_path / "project"
    source.mkdir(exist_ok=True)
    (source / "note.txt").write_text("one\n")
    path = source / name
    _write(path, body)
    return path


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    path = _recipe(
        tmp_path,
        """
version: 1
execution:
  unexpected: true
  commands:
    - id: run
      run: ["python", "-c", "print(1)"]
""",
    )
    with pytest.raises(UsageError, match="unexpected"):
        validate_file(path)


def test_duplicate_command_ids_are_rejected(tmp_path: Path) -> None:
    path = _recipe(
        tmp_path,
        """
version: 1
execution:
  commands:
    - id: build
      run: ["python", "-c", "print(1)"]
    - id: build
      run: ["python", "-c", "print(2)"]
""",
    )
    with pytest.raises(UsageError, match="unique"):
        validate_file(path)


def test_command_must_be_an_argument_list(tmp_path: Path) -> None:
    path = _recipe(
        tmp_path,
        """
version: 1
execution:
  commands:
    - id: build
      run: "python -c print(1)"
""",
    )
    with pytest.raises(UsageError, match="list"):
        validate_file(path)


def test_missing_interpreter_does_not_create_a_session(tmp_path: Path, monkeypatch) -> None:
    path = _recipe(
        tmp_path,
        """
version: 1
environment:
  type: venv
  python: sandbox-missing-python
execution:
  commands:
    - id: run
      run: ["python", "-c", "print(1)"]
""",
    )
    data = tmp_path / "data"
    monkeypatch.setenv("LUPAXA_SANDBOX_DATA", str(data))
    with pytest.raises(UsageError, match="not found"):
        validate_file(path)
    with pytest.raises(UsageError, match="not found"):
        run_recipe_file(path, data_dir=data)
    assert not (data / "sessions").exists()


def test_commands_run_in_order_and_stop_on_error(tmp_path: Path) -> None:
    path = _recipe(
        tmp_path,
        f"""
version: 1
sandbox:
  name: ordered
workspace:
  source: .
execution:
  stop_on_error: true
  commands:
    - id: write
      run: ["{sys.executable}", "-c", "open('generated.txt','w').write('ok')"]
    - id: fail
      run: ["{sys.executable}", "-c", "raise SystemExit(4)"]
    - id: later
      run: ["{sys.executable}", "-c", "open('later.txt','w').write('no')"]
""",
    )
    data = tmp_path / "data"
    result = run_recipe_file(path, data_dir=data)
    workspace = SessionStore(data).get("ordered").workspace
    assert (workspace / "generated.txt").read_text() == "ok"
    assert not (workspace / "later.txt").exists()
    assert [item.status for item in result.outcomes] == ["completed", "failed", "skipped"]
    assert result.outcomes[1].exit_code == 4


def test_stop_on_error_false_continues(tmp_path: Path) -> None:
    path = _recipe(
        tmp_path,
        f"""
version: 1
sandbox:
  name: keep-going
execution:
  stop_on_error: false
  commands:
    - id: fail
      run: ["{sys.executable}", "-c", "raise SystemExit(4)"]
    - id: later
      run: ["{sys.executable}", "-c", "open('later.txt','w').write('yes')"]
""",
    )
    data = tmp_path / "data"
    run_recipe_file(path, data_dir=data)
    workspace = SessionStore(data).get("keep-going").workspace
    assert (workspace / "later.txt").read_text() == "yes"


def test_success_destroy_removes_the_session(tmp_path: Path) -> None:
    path = _recipe(
        tmp_path,
        f"""
version: 1
sandbox:
  name: gone
cleanup:
  on_success: destroy
execution:
  commands:
    - id: run
      run: ["{sys.executable}", "-c", "print('ok')"]
""",
    )
    data = tmp_path / "data"
    result = run_recipe_file(path, data_dir=data)
    assert result.destroyed is True
    with pytest.raises(NotFoundError):
        SessionStore(data).get("gone")


def test_effective_config_redacts_secrets(tmp_path: Path, monkeypatch) -> None:
    path = _recipe(
        tmp_path,
        f"""
version: 1
sandbox:
  name: recorded
execution:
  timeout_seconds: 22
  environment:
    set:
      AWS_SECRET_ACCESS_KEY: super-secret
      BUILD_MODE: testing
  commands:
    - id: run
      run: ["{sys.executable}", "-c", "print('ok')"]
""",
    )
    user = tmp_path / "user.yml"
    user.write_text("version: 1\nexecution:\n  timeout_seconds: 11\n")
    monkeypatch.setenv("LUPAXA_SANDBOX_CONFIG", str(user))
    data = tmp_path / "data"
    run_recipe_file(path, data_dir=data, cli={"execution": {"timeout_seconds": 33}})
    session = SessionStore(data).get("recorded")
    payload = json.loads((session.root / "effective-config.json").read_text())
    assert payload["execution"]["timeout_seconds"] == 33
    values = payload["execution"]["environment"]["set"]
    assert values["AWS_SECRET_ACCESS_KEY"] == "***"
    assert values["BUILD_MODE"] == "testing"
    assert "super-secret" not in (session.root / "effective-config.json").read_text()


def test_user_timeout_survives_when_the_recipe_omits_it() -> None:
    recipe = resolve_recipe(
        user={"version": 1, "execution": {"timeout_seconds": 11}},
        recipe={"version": 1, "execution": {"commands": [{"id": "run", "run": ["python"]}]}},
    )
    assert recipe.execution.timeout_seconds == 11


def test_venv_commands_use_the_environment(tmp_path: Path, monkeypatch) -> None:
    path = _recipe(
        tmp_path,
        f"""
version: 1
sandbox:
  name: env
environment:
  type: venv
  python: "{sys.executable}"
  install:
    - pytest
execution:
  commands:
    - id: prefix
      run: ["python", "-c", "import sys; print(sys.prefix)"]
""",
    )
    installed: list[list[str]] = []

    def fake_install(python: Path, workspace: Path, packages: list[str]) -> None:
        installed.append(packages)

    monkeypatch.setattr("lupaxa.sandbox.recipes.runner.install_packages", fake_install)
    data = tmp_path / "data"
    result = run_recipe_file(path, data_dir=data)
    session = SessionStore(data).get("env")
    assert installed == [["pytest"]]
    assert (session.workspace / ".venv").is_dir()
    assert result.outcomes[0].status == "completed"
    log = session.executions[0].stdout
    assert str(session.workspace / ".venv") in log
    metadata = json.loads((session.root / "environment.json").read_text())
    assert metadata["secure"] is False
    assert "not a security boundary" in metadata["note"].lower()

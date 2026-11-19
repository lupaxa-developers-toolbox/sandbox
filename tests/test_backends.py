"""Stronger backends are optional, and a missing restriction is a refusal."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lupaxa.sandbox.exceptions import UsageError
from lupaxa.sandbox.execution.container import container_argv
from lupaxa.sandbox.execution.policy import IsolationRequest
from lupaxa.sandbox.execution.registry import open_backend
from lupaxa.sandbox.execution.restricted import sandbox_exec_works, sandbox_profile


def test_local_refuses_a_network_denial() -> None:
    request = IsolationRequest(backend="local", network="deny")
    with pytest.raises(UsageError, match="will not"):
        open_backend(request)


def test_local_refuses_a_memory_limit() -> None:
    request = IsolationRequest(backend="local", memory_mb=128)
    with pytest.raises(UsageError, match="cannot enforce"):
        open_backend(request)


def test_missing_docker_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "lupaxa.sandbox.execution.registry.engine_available",
        lambda engine: False,
    )
    request = IsolationRequest(backend="docker", image="python:3.12-slim")
    with pytest.raises(UsageError, match="will not fall back"):
        open_backend(request)


def test_restricted_refuses_resource_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "lupaxa.sandbox.execution.registry.restricted_available",
        lambda: True,
    )
    request = IsolationRequest(backend="restricted", memory_mb=64)
    with pytest.raises(UsageError, match="cannot enforce"):
        open_backend(request)


def test_container_command_does_not_pull_or_weaken(tmp_path: Path) -> None:
    argv = container_argv(
        engine="docker",
        image="python:3.12-slim",
        workspace=tmp_path,
        command=["python", "-c", "print(1)"],
        network="deny",
        memory_mb=128,
        cpus=0.5,
        env={"BUILD_MODE": "testing"},
    )
    assert "--pull" not in argv
    assert "--privileged" not in argv
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--memory") + 1] == "128m"
    assert "python:3.12-slim" in argv
    assert any(item.endswith(f"{tmp_path}:/workspace:rw") for item in argv)
    assert "BUILD_MODE=testing" in argv


def test_profile_confines_writes_and_can_deny_network(tmp_path: Path) -> None:
    denied = sandbox_profile(tmp_path, network="deny")
    allowed = sandbox_profile(tmp_path, network="allow")
    assert f'(subpath "{tmp_path}")' in denied
    assert "(deny network*)" in denied
    assert "(deny network*)" not in allowed


def test_restricted_blocks_a_write_outside_the_workspace(tmp_path: Path) -> None:
    if not sandbox_exec_works():
        pytest.skip("sandbox-exec is not available")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    backend = open_backend(IsolationRequest(backend="restricted"))
    denied = backend.run(
        argv=[sys.executable, "-c", f"open({str(outside)!r}, 'w').write('no')"],
        cwd=workspace,
        timeout_seconds=30,
        grace_seconds=1,
        env=None,
        stdin_path=None,
        stream=False,
        workspace_root=workspace,
    )
    assert denied.backend == "restricted"
    assert denied.exit_code not in (0, None)
    assert not outside.exists()
    allowed = backend.run(
        argv=[sys.executable, "-c", "open('inside.txt', 'w').write('yes')"],
        cwd=workspace,
        timeout_seconds=30,
        grace_seconds=1,
        env=None,
        stdin_path=None,
        stream=False,
        workspace_root=workspace,
    )
    assert allowed.exit_code == 0
    assert (workspace / "inside.txt").read_text() == "yes"
    assert "not a complete confinement" in backend.notice().lower()

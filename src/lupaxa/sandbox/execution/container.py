"""Docker and Podman execution. Images are never pulled."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from lupaxa.sandbox.exceptions import UsageError
from lupaxa.sandbox.execution.base import CONTAINER_CAPABILITIES
from lupaxa.sandbox.execution.local import LocalBackend
from lupaxa.sandbox.execution.policy import IsolationRequest

_NOTICE = (
    "The container backend runs the command in a container with the workspace mounted. "
    "The container engine and image are trusted. Sandbox does not pull images. "
    "This is not a guarantee against kernel or engine escapes."
)


def engine_available(engine: str) -> bool:
    """True when the engine answers ``info``. A name on PATH is not enough."""
    binary = shutil.which(engine)
    if binary is None:
        return False
    completed = subprocess.run(
        [binary, "info"],
        capture_output=True,
        timeout=8,
        check=False,
    )
    return completed.returncode == 0


def image_present(engine: str, image: str) -> bool:
    binary = shutil.which(engine)
    if binary is None:
        return False
    completed = subprocess.run(
        [binary, "image", "inspect", image],
        capture_output=True,
        timeout=8,
        check=False,
    )
    return completed.returncode == 0


def container_argv(
    *,
    engine: str,
    image: str,
    workspace: Path,
    command: list[str],
    network: str,
    memory_mb: int | None,
    cpus: float | None,
    env: dict[str, str] | None,
) -> list[str]:
    """Build a ``run`` command. There is no pull and no privileged mode."""
    argv = [
        engine,
        "run",
        "--rm",
        "-v",
        f"{Path(workspace).resolve()}:/workspace:rw",
        "-w",
        "/workspace",
    ]
    if network == "deny":
        argv.extend(["--network", "none"])
    if memory_mb is not None:
        argv.extend(["--memory", f"{memory_mb}m"])
    if cpus is not None:
        argv.extend(["--cpus", str(cpus)])
    for key, value in (env or {}).items():
        argv.extend(["-e", f"{key}={value}"])
    argv.append(image)
    argv.extend(command)
    return argv


class ContainerBackend:
    """One container engine. A missing image or engine is an error."""

    def __init__(self, request: IsolationRequest) -> None:
        self.request = request
        self.name = request.backend
        self.engine = request.backend

    def capabilities(self) -> dict[str, bool]:
        return dict(CONTAINER_CAPABILITIES)

    def applied(self, request: IsolationRequest | None = None) -> dict[str, bool]:
        current = request or self.request
        applied = self.capabilities()
        applied["network_restrictions"] = current.network == "deny"
        applied["resource_limits"] = current.memory_mb is not None or current.cpus is not None
        return applied

    def notice(self, request: IsolationRequest | None = None) -> str:
        current = request or self.request
        if current.network == "deny":
            return _NOTICE + " Network access is denied for this command."
        return _NOTICE

    def run(
        self,
        *,
        argv: list[str],
        cwd: Path,
        timeout_seconds: float | None,
        grace_seconds: float,
        env: dict[str, str] | None,
        stdin_path: Path | None,
        stream: bool,
        workspace_root: Path | None = None,
        on_pid: object = None,
    ):
        image = self.request.image
        if not image:
            raise UsageError("A container backend requires --image. Sandbox does not pull images.")
        if not image_present(self.engine, image):
            raise UsageError(
                f"Image {image} is not available locally. Sandbox does not pull container images."
            )
        root = Path(workspace_root) if workspace_root is not None else Path(cwd)
        wrapped = container_argv(
            engine=self.engine,
            image=image,
            workspace=root,
            command=list(argv),
            network=self.request.network,
            memory_mb=self.request.memory_mb,
            cpus=self.request.cpus,
            env=env,
        )
        return LocalBackend(name=self.name).run(
            argv=wrapped,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            grace_seconds=grace_seconds,
            env=None,
            stdin_path=stdin_path,
            stream=stream,
            workspace_root=root,
            on_pid=on_pid,  # type: ignore[arg-type]
        )

    def run_shell(self, *, script: str, **kwargs: object):
        return self.run(argv=["/bin/sh", "-c", script], **kwargs)  # type: ignore[arg-type]

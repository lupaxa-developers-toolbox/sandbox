"""Choose a backend by probe. A missing backend never falls back to local."""

from __future__ import annotations

from dataclasses import dataclass

from lupaxa.sandbox.exceptions import UsageError
from lupaxa.sandbox.execution.base import (
    CONTAINER_CAPABILITIES,
    LOCAL_CAPABILITIES,
    RESTRICTED_CAPABILITIES,
)
from lupaxa.sandbox.execution.container import ContainerBackend, engine_available
from lupaxa.sandbox.execution.local import LocalBackend
from lupaxa.sandbox.execution.policy import IsolationRequest, require_supported
from lupaxa.sandbox.execution.restricted import RestrictedBackend, restricted_available

_THREATS = {
    "local": (
        "The local backend runs the command as your user in the staged directory. "
        "It does not restrict files, network, or processes. "
        "It is not a way to run untrusted code."
    ),
    "restricted": (
        "The restricted backend uses sandbox-exec or bubblewrap when a probe succeeds. "
        "It denies writes outside the workspace and can deny network. "
        "It does not limit CPU or memory, "
        "and it is not a complete confinement boundary."
    ),
    "docker": (
        "Docker runs the command in a container. Only the workspace is mounted from the host. "
        "Sandbox does not pull images and does not retry without a requested limit. "
        "The engine and image are trusted."
    ),
    "podman": (
        "Podman runs the command in a container. Only the workspace is mounted from the host. "
        "Sandbox does not pull images and does not retry without a requested limit. "
        "The engine and image are trusted."
    ),
}


@dataclass(frozen=True)
class BackendStatus:
    """Whether a backend can be selected, and the limits it is able to enforce."""

    name: str
    available: bool
    reason: str
    capabilities: dict[str, bool]
    threat_model: str


def catalog() -> list[BackendStatus]:
    """Probe each backend. Availability is the probe result."""
    return [_status(name) for name in ("local", "restricted", "docker", "podman")]


def describe(name: str) -> BackendStatus:
    if name not in _THREATS:
        raise UsageError(f"Unknown backend: {name}")
    return _status(name)


def _status(name: str) -> BackendStatus:
    if name == "local":
        return BackendStatus("local", True, "built in", dict(LOCAL_CAPABILITIES), _THREATS["local"])
    if name == "restricted":
        ready = restricted_available()
        reason = "ready" if ready else "sandbox-exec and bubblewrap probes failed"
        return BackendStatus(name, ready, reason, dict(RESTRICTED_CAPABILITIES), _THREATS[name])
    ready = engine_available(name)
    reason = "ready" if ready else f"{name} info failed or {name} was not found"
    return BackendStatus(name, ready, reason, dict(CONTAINER_CAPABILITIES), _THREATS[name])


def open_backend(request: IsolationRequest) -> LocalBackend | RestrictedBackend | ContainerBackend:
    """Return a backend that can enforce ``request``, or refuse."""
    status = describe(request.backend)
    if not status.available:
        raise UsageError(
            f"Backend {request.backend} is not available. "
            "Sandbox will not fall back to a weaker backend. "
            f"{status.reason}."
        )
    require_supported(request, status.capabilities)
    if request.backend == "local":
        return LocalBackend()
    if request.backend == "restricted":
        return RestrictedBackend(request)
    if request.backend in {"docker", "podman"} and not request.image:
        raise UsageError("A container backend requires --image. Sandbox does not pull images.")
    return ContainerBackend(request)

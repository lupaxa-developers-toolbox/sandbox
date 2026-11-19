"""What a run is allowed to ask for. Unsupported restrictions are refusals."""

from __future__ import annotations

from dataclasses import dataclass

from lupaxa.sandbox.exceptions import UsageError


@dataclass(frozen=True)
class IsolationRequest:
    """The isolation a command asked for. The default is the local backend."""

    backend: str = "local"
    network: str = "allow"
    memory_mb: int | None = None
    cpus: float | None = None
    image: str | None = None


def refusal_reason(request: IsolationRequest, capabilities: dict[str, bool]) -> str | None:
    """Return why this request must not run, or None when the backend can enforce it."""
    if request.network == "deny" and not capabilities["network_restrictions"]:
        return (
            f"Backend {request.backend} cannot enforce a network denial "
            "and will not run with weaker protection."
        )
    limited = request.memory_mb is not None or request.cpus is not None
    if limited and not capabilities["resource_limits"]:
        return (
            f"Backend {request.backend} cannot enforce CPU or memory limits "
            "and will not run with weaker protection."
        )
    return None


def require_supported(request: IsolationRequest, capabilities: dict[str, bool]) -> None:
    reason = refusal_reason(request, capabilities)
    if reason is not None:
        raise UsageError(reason)

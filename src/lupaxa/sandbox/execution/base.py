"""Execution results shared by every backend."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExecutionResult:
    """One finished command. Output is decoded as UTF-8 with replacement."""

    exit_code: int | None
    stdout: str
    stderr: str
    termination_reason: str
    backend: str
    duration_seconds: float
    command: list[str]


LOCAL_CAPABILITIES: dict[str, bool] = {
    "workspace_separation": True,
    "filesystem_restrictions": False,
    "network_restrictions": False,
    "process_restrictions": False,
    "resource_limits": False,
}

LOCAL_NOTICE = (
    "Workspace mode is not a security boundary. The command ran with your user "
    "permissions and can read, write, and reach the network outside the workspace."
)

RESTRICTED_CAPABILITIES: dict[str, bool] = {
    "workspace_separation": True,
    "filesystem_restrictions": True,
    "network_restrictions": True,
    "process_restrictions": False,
    "resource_limits": False,
}

CONTAINER_CAPABILITIES: dict[str, bool] = {
    "workspace_separation": True,
    "filesystem_restrictions": True,
    "network_restrictions": True,
    "process_restrictions": True,
    "resource_limits": True,
}

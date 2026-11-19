"""Recipe and configuration models. Unknown fields are errors."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lupaxa.sandbox.execution.base import (
    CONTAINER_CAPABILITIES,
    LOCAL_CAPABILITIES,
    RESTRICTED_CAPABILITIES,
)
from lupaxa.sandbox.execution.policy import IsolationRequest, refusal_reason
from lupaxa.sandbox.workspace.patterns import DEFAULT_STAGING_EXCLUDES, DEFAULT_TRACKING_EXCLUDES

_BACKEND_CAPABILITIES = {
    "local": LOCAL_CAPABILITIES,
    "restricted": RESTRICTED_CAPABILITIES,
    "docker": CONTAINER_CAPABILITIES,
    "podman": CONTAINER_CAPABILITIES,
}

_COMMAND_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class StrictModel(BaseModel):
    """Reject keys the schema does not define."""

    model_config = ConfigDict(extra="forbid")


class LimitsSpec(StrictModel):
    max_files: int = 100_000
    max_size_mb: int = 2048


class StagingSpec(StrictModel):
    exclude: list[str] = Field(default_factory=lambda: list(DEFAULT_STAGING_EXCLUDES))


class WorkspaceSpec(StrictModel):
    source: str = "."
    strategy: Literal["copy", "empty"] = "copy"
    staging: StagingSpec = Field(default_factory=StagingSpec)
    limits: LimitsSpec = Field(default_factory=LimitsSpec)


class EnvironmentSpec(StrictModel):
    type: Literal["none", "venv", "existing"] = "none"
    python: str | None = None
    install: list[str] = Field(default_factory=list)


class EnvVars(StrictModel):
    inherit: bool = True
    set: dict[str, str] = Field(default_factory=dict)
    unset: list[str] = Field(default_factory=list)

    @field_validator("set", mode="before")
    @classmethod
    def stringify_values(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        result: dict[str, str] = {}
        for key, item in value.items():
            if isinstance(item, bool) or not isinstance(item, str | int | float):
                raise ValueError(f"value for {key} must be a string")
            result[str(key)] = str(item)
        return result

    @model_validator(mode="after")
    def names_are_valid(self) -> EnvVars:
        for name in [*self.set, *self.unset]:
            if _ENV_NAME.fullmatch(name) is None or "\0" in self.set.get(name, ""):
                raise ValueError(f"invalid environment name or value: {name}")
        return self


class CommandSpec(StrictModel):
    id: str
    run: list[str]

    @field_validator("id")
    @classmethod
    def id_is_safe(cls, value: str) -> str:
        if _COMMAND_ID.fullmatch(value) is None:
            raise ValueError("command id must start with a letter or digit")
        return value

    @field_validator("run")
    @classmethod
    def run_is_an_argument_list(cls, value: list[str]) -> list[str]:
        if not value or any(not item for item in value):
            raise ValueError("run must be a list of non-empty arguments")
        return value


class ResourcesSpec(StrictModel):
    memory_mb: int | None = None
    cpus: float | None = None

    @model_validator(mode="after")
    def limits_are_positive(self) -> ResourcesSpec:
        if self.memory_mb is not None and self.memory_mb <= 0:
            raise ValueError("memory_mb must be greater than zero")
        if self.cpus is not None and self.cpus <= 0:
            raise ValueError("cpus must be greater than zero")
        return self


class ExecutionSpec(StrictModel):
    timeout_seconds: int = 300
    stop_on_error: bool = True
    backend: Literal["local", "restricted", "docker", "podman"] = "local"
    network: Literal["allow", "deny"] = "allow"
    image: str | None = None
    resources: ResourcesSpec = Field(default_factory=ResourcesSpec)
    commands: list[CommandSpec] = Field(default_factory=list)
    environment: EnvVars = Field(default_factory=EnvVars)


class TrackingSpec(StrictModel):
    enabled: bool = True
    exclude: list[str] = Field(default_factory=lambda: list(DEFAULT_TRACKING_EXCLUDES))
    hash_algorithm: Literal["sha256"] = "sha256"


class OutputsSpec(StrictModel):
    include: list[str] = Field(default_factory=list)


class CleanupSpec(StrictModel):
    on_success: Literal["retain", "destroy"] = "retain"
    on_failure: Literal["retain", "destroy"] = "retain"
    max_age_days: int = 14


class SandboxMeta(StrictModel):
    name: str | None = None
    description: str = ""


class InterfaceSpec(StrictModel):
    color: Literal["auto", "always", "never"] = "auto"
    confirm_destructive: bool = True


class Recipe(StrictModel):
    version: Literal[1]
    sandbox: SandboxMeta = Field(default_factory=SandboxMeta)
    workspace: WorkspaceSpec = Field(default_factory=WorkspaceSpec)
    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)
    execution: ExecutionSpec = Field(default_factory=ExecutionSpec)
    tracking: TrackingSpec = Field(default_factory=TrackingSpec)
    outputs: OutputsSpec = Field(default_factory=OutputsSpec)
    cleanup: CleanupSpec = Field(default_factory=CleanupSpec)
    interface: InterfaceSpec = Field(default_factory=InterfaceSpec)

    @model_validator(mode="after")
    def check_relationships(self) -> Recipe:
        ids = [command.id for command in self.execution.commands]
        if len(ids) != len(set(ids)):
            raise ValueError("command ids must be unique")
        if self.environment.install and self.environment.type != "venv":
            raise ValueError("install is only valid when environment.type is venv")
        if self.environment.type in {"venv", "existing"} and not self.environment.python:
            raise ValueError("environment.python is required for this environment type")
        request = IsolationRequest(
            backend=self.execution.backend,
            network=self.execution.network,
            memory_mb=self.execution.resources.memory_mb,
            cpus=self.execution.resources.cpus,
            image=self.execution.image,
        )
        reason = refusal_reason(request, _BACKEND_CAPABILITIES[self.execution.backend])
        if reason is not None:
            raise ValueError(reason)
        if self.execution.backend in {"docker", "podman"} and not self.execution.image:
            raise ValueError("execution.image is required for a container backend")
        patterns = [*self.outputs.include, *self.workspace.staging.exclude, *self.tracking.exclude]
        for pattern in patterns:
            escaped = pattern.startswith("/") or ".." in pattern.split("/")
            if not pattern or "\0" in pattern or escaped:
                raise ValueError(f"invalid path pattern: {pattern}")
        return self

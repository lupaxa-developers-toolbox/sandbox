"""Sandbox failures and the process exit codes they map to."""

from __future__ import annotations


class SandboxError(Exception):
    """Base error. ``exit_code`` is what the CLI returns."""

    exit_code = 1


class UsageError(SandboxError):
    """The command line or a requested path is not valid."""

    exit_code = 2


class NotFoundError(SandboxError):
    """The named session does not exist."""

    exit_code = 3


class ConflictError(SandboxError):
    """A filesystem or apply conflict blocked the operation."""

    exit_code = 4


class StagingError(SandboxError):
    """The workspace could not be staged."""

    exit_code = 1


class SessionBusy(SandboxError):
    """A session already has a filesystem-mutating command running."""

    exit_code = 1

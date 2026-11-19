"""OS-enforced write limits where a probe says the tool actually works."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from lupaxa.sandbox.exceptions import UsageError
from lupaxa.sandbox.execution.base import RESTRICTED_CAPABILITIES
from lupaxa.sandbox.execution.local import LocalBackend
from lupaxa.sandbox.execution.policy import IsolationRequest

_NOTICE = (
    "The restricted backend denies writes outside the workspace. It still runs as your "
    "user and can read files the profile allows. It is not a complete confinement boundary."
)


def sandbox_profile(workspace: Path, *, network: str) -> str:
    """Seatbelt profile. Writes are limited to ``workspace``. Network is optional."""
    root = str(Path(workspace).resolve())
    if any(character in root for character in '"()\n'):
        raise UsageError(f"Workspace path cannot be expressed in a sandbox profile: {root}")
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process*)",
        "(allow signal)",
        "(allow sysctl-read)",
        "(allow mach*)",
        "(allow ipc-posix*)",
        "(allow file-read*)",
        "(allow file-ioctl)",
        f'(allow file-write* (subpath "{root}"))',
    ]
    if network == "deny":
        lines.append("(deny network*)")
    else:
        lines.append("(allow network*)")
    return "\n".join(lines) + "\n"


def sandbox_exec_works() -> bool:
    """True only when ``sandbox-exec`` can apply a profile. The OS name is not enough."""
    binary = shutil.which("sandbox-exec")
    if binary is None:
        return False
    true_bin = shutil.which("true") or "/usr/bin/true"
    completed = subprocess.run(
        [binary, "-p", "(version 1)(allow default)", true_bin],
        capture_output=True,
        timeout=5,
        check=False,
    )
    return completed.returncode == 0


def bwrap_works() -> bool:
    """True only when bubblewrap can start a process. A binary on PATH is not enough."""
    binary = shutil.which("bwrap")
    if binary is None:
        return False
    completed = subprocess.run(
        [binary, "--die-with-parent", "--unshare-pid", "--ro-bind", "/", "/", "true"],
        capture_output=True,
        timeout=5,
        check=False,
    )
    return completed.returncode == 0


def restricted_available() -> bool:
    return sandbox_exec_works() or bwrap_works()


def bwrap_argv(*, workspace: Path, command: list[str], network: str) -> list[str]:
    """Bubblewrap command. System directories are read-only. The workspace is writable."""
    root = str(Path(workspace).resolve())
    binary = shutil.which("bwrap") or "bwrap"
    argv = [binary, "--die-with-parent", "--unshare-pid"]
    if network == "deny":
        argv.append("--unshare-net")
    for mount in ("/usr", "/bin", "/lib", "/lib64", "/opt", "/etc", "/System", "/Library"):
        if Path(mount).is_dir():
            argv.extend(["--ro-bind", mount, mount])
    argv.extend(
        [
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--bind",
            root,
            "/workspace",
            "--chdir",
            "/workspace",
            "--",
            *command,
        ]
    )
    return argv


class RestrictedBackend:
    """Write confinement through sandbox-exec or bubblewrap, chosen by a probe."""

    name = "restricted"

    def __init__(self, request: IsolationRequest) -> None:
        self.request = request

    def capabilities(self) -> dict[str, bool]:
        return dict(RESTRICTED_CAPABILITIES)

    def applied(self, request: IsolationRequest | None = None) -> dict[str, bool]:
        current = request or self.request
        applied = self.capabilities()
        applied["network_restrictions"] = current.network == "deny"
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
        root = Path(workspace_root) if workspace_root is not None else Path(cwd)
        wrapped = _wrap(root, list(argv), self.request.network)
        return LocalBackend(name=self.name).run(
            argv=wrapped,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            grace_seconds=grace_seconds,
            env=env,
            stdin_path=stdin_path,
            stream=stream,
            workspace_root=root,
            on_pid=on_pid,  # type: ignore[arg-type]
        )

    def run_shell(self, *, script: str, **kwargs: object):
        shell = ["/bin/sh", "-c", script]
        return self.run(argv=shell, **kwargs)  # type: ignore[arg-type]


def _wrap(workspace: Path, command: list[str], network: str) -> list[str]:
    if sandbox_exec_works():
        binary = shutil.which("sandbox-exec")
        if binary is None:
            raise UsageError("sandbox-exec is not available. Sandbox will not fall back.")
        return [binary, "-p", sandbox_profile(workspace, network=network), *command]
    if bwrap_works():
        return bwrap_argv(workspace=workspace, command=command, network=network)
    raise UsageError(
        "Backend restricted is not available. Sandbox will not fall back to a weaker backend."
    )

"""Run a command on the host, inside a staged workspace."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from lupaxa.sandbox.exceptions import UsageError
from lupaxa.sandbox.execution.base import LOCAL_CAPABILITIES, LOCAL_NOTICE, ExecutionResult

_MAX_LOG = 10 * 1024 * 1024
_POLL = 0.02


class LocalBackend:
    """Host subprocesses. This is workspace separation, not a security boundary."""

    def __init__(self, name: str = "local") -> None:
        self.name = name

    def capabilities(self) -> dict[str, bool]:
        return dict(LOCAL_CAPABILITIES)

    def applied(self, request: object | None = None) -> dict[str, bool]:
        del request
        return self.capabilities()

    def notice(self, request: object | None = None) -> str:
        del request
        return LOCAL_NOTICE

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
        on_pid: Callable[[int], None] | None = None,
    ) -> ExecutionResult:
        root = Path(workspace_root) if workspace_root is not None else Path(cwd)
        _require_inside(Path(cwd), root)
        return self._spawn(
            argv=list(argv),
            cwd=Path(cwd),
            timeout_seconds=timeout_seconds,
            grace_seconds=grace_seconds,
            env=env,
            stdin_path=stdin_path,
            stream=stream,
            on_pid=on_pid,
        )

    def run_shell(
        self,
        *,
        script: str,
        cwd: Path,
        timeout_seconds: float | None,
        grace_seconds: float,
        env: dict[str, str] | None,
        stdin_path: Path | None,
        stream: bool,
        workspace_root: Path | None = None,
        on_pid: Callable[[int], None] | None = None,
    ) -> ExecutionResult:
        shell = ["cmd", "/c"] if os.name == "nt" else ["/bin/sh", "-c"]
        return self.run(
            argv=[*shell, script],
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            grace_seconds=grace_seconds,
            env=env,
            stdin_path=stdin_path,
            stream=stream,
            workspace_root=workspace_root,
            on_pid=on_pid,
        )

    def _spawn(
        self,
        *,
        argv: list[str],
        cwd: Path,
        timeout_seconds: float | None,
        grace_seconds: float,
        env: dict[str, str] | None,
        stdin_path: Path | None,
        stream: bool,
        on_pid: Callable[[int], None] | None,
    ) -> ExecutionResult:
        started = time.monotonic()
        try:
            with _stdin(stdin_path) as stdin_handle:
                return self._run_process(
                    argv=argv,
                    cwd=cwd,
                    timeout_seconds=timeout_seconds,
                    grace_seconds=grace_seconds,
                    env=env,
                    stdin_handle=stdin_handle,
                    stream=stream,
                    on_pid=on_pid,
                    started=started,
                )
        except FileNotFoundError:
            return ExecutionResult(
                exit_code=None,
                stdout="",
                stderr=f"Executable not found: {argv[0]}\n",
                termination_reason="completed",
                backend=self.name,
                duration_seconds=time.monotonic() - started,
                command=argv,
            )

    def _run_process(
        self,
        *,
        argv: list[str],
        cwd: Path,
        timeout_seconds: float | None,
        grace_seconds: float,
        env: dict[str, str] | None,
        stdin_handle: BinaryIO | int,
        stream: bool,
        on_pid: Callable[[int], None] | None,
        started: float,
    ) -> ExecutionResult:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=stdin_handle,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        if on_pid is not None:
            on_pid(process.pid)
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = bytearray()
        stderr = bytearray()
        pumps = [
            threading.Thread(
                target=_pump,
                args=(process.stdout, stdout, stream, sys.stdout),
                daemon=True,
            ),
            threading.Thread(
                target=_pump,
                args=(process.stderr, stderr, stream, sys.stderr),
                daemon=True,
            ),
        ]
        for pump in pumps:
            pump.start()
        reason = "completed"
        try:
            while process.poll() is None:
                if timeout_seconds is not None and time.monotonic() - started >= timeout_seconds:
                    _terminate(process, grace_seconds)
                    reason = "timeout"
                    break
                time.sleep(_POLL)
            process.wait()
        except KeyboardInterrupt:
            _terminate(process, grace_seconds)
            reason = "interrupted"
            raise
        finally:
            for pump in pumps:
                pump.join(timeout=2)
        return ExecutionResult(
            exit_code=process.returncode,
            stdout=bytes(stdout).decode("utf-8", errors="replace"),
            stderr=bytes(stderr).decode("utf-8", errors="replace"),
            termination_reason=reason,
            backend=self.name,
            duration_seconds=time.monotonic() - started,
            command=argv,
        )


@contextmanager
def _stdin(path: Path | None) -> Iterator[BinaryIO | int]:
    if path is None:
        yield subprocess.DEVNULL
        return
    with path.open("rb") as handle:
        yield handle


def _require_inside(cwd: Path, root: Path) -> None:
    try:
        cwd.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise UsageError("Working directory is outside the workspace") from exc
    if not cwd.is_dir():
        raise UsageError(f"Working directory does not exist: {cwd}")


def _pump(stream: object, sink: bytearray, echo: bool, target: object) -> None:
    reader = stream
    assert hasattr(reader, "read")
    while True:
        chunk = reader.read(8192)
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        if len(sink) < _MAX_LOG:
            room = _MAX_LOG - len(sink)
            sink.extend(chunk[:room])
        if echo and hasattr(target, "buffer"):
            target.buffer.write(chunk)
            target.buffer.flush()
    if hasattr(reader, "close"):
        reader.close()


def _terminate(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=grace_seconds)
            return
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            process.wait()
        return
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

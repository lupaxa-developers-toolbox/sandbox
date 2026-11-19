"""Local backend runs argument arrays and records timeouts."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from lupaxa.sandbox.exceptions import UsageError
from lupaxa.sandbox.execution.local import LocalBackend


def test_captures_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    result = LocalBackend().run(
        argv=[
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('out\\n'); sys.stderr.write('err\\n'); "
            + "raise SystemExit(3)",
        ],
        cwd=tmp_path,
        timeout_seconds=30,
        grace_seconds=1,
        env=None,
        stdin_path=None,
        stream=False,
    )
    assert result.exit_code == 3
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
    assert result.termination_reason == "completed"
    assert result.backend == "local"


def test_shell_is_not_implied(tmp_path: Path) -> None:
    result = LocalBackend().run(
        argv=[sys.executable, "-c", "import sys; print(sys.argv[1])", "*"],
        cwd=tmp_path,
        timeout_seconds=30,
        grace_seconds=1,
        env=None,
        stdin_path=None,
        stream=False,
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "*"


@pytest.mark.skipif(os.name != "posix", reason="explicit shell uses /bin/sh")
def test_explicit_shell_expands(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a\n")
    result = LocalBackend().run_shell(
        script="echo *",
        cwd=tmp_path,
        timeout_seconds=30,
        grace_seconds=1,
        env=None,
        stdin_path=None,
        stream=False,
    )
    assert result.exit_code == 0
    assert "a.txt" in result.stdout
    assert "*" not in result.stdout


def test_timeout_kills_the_process(tmp_path: Path) -> None:
    result = LocalBackend().run(
        argv=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        timeout_seconds=1,
        grace_seconds=1,
        env=None,
        stdin_path=None,
        stream=False,
    )
    assert result.termination_reason == "timeout"
    assert result.duration_seconds < 15


def test_cwd_must_stay_inside_the_workspace(tmp_path: Path) -> None:
    with pytest.raises(UsageError):
        LocalBackend().run(
            argv=[sys.executable, "-c", "print(1)"],
            cwd=tmp_path,
            workspace_root=tmp_path / "workspace",
            timeout_seconds=5,
            grace_seconds=1,
            env=None,
            stdin_path=None,
            stream=False,
        )


def test_local_backend_reports_workspace_separation_only() -> None:
    caps = LocalBackend().capabilities()
    assert caps["workspace_separation"] is True
    assert caps["filesystem_restrictions"] is False
    assert caps["network_restrictions"] is False
    assert caps["process_restrictions"] is False
    assert caps["resource_limits"] is False

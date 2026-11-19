"""A retained session records execution and can be inspected later."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lupaxa.sandbox.exceptions import NotFoundError, SessionBusy
from lupaxa.sandbox.service import create_session, destroy_session, exec_session, run_once
from lupaxa.sandbox.sessions.store import SessionStore


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "project"
    source.mkdir()
    (source / "note.txt").write_text("one\n")
    (source / "drop.txt").write_text("gone\n")
    return source


def test_run_stages_executes_and_leaves_the_source_untouched(tmp_path: Path) -> None:
    source = _source(tmp_path)
    before = {path.name: path.read_text() for path in source.iterdir()}
    data = tmp_path / "data"

    report = run_once(
        source=source,
        name="experiment-1",
        command=[
            sys.executable,
            "-c",
            (
                "from pathlib import Path\n"
                "Path('created.txt').write_text('new\\n')\n"
                "Path('note.txt').write_text('two\\n')\n"
                "Path('drop.txt').unlink()\n"
                "import os; os.chmod('note.txt', 0o755)\n"
            ),
        ],
        data_dir=data,
        timeout_seconds=30,
    )

    assert {path.name: path.read_text() for path in source.iterdir()} == before
    categories = {change.path: change.category for change in report.changes}
    assert categories["created.txt"] == "added"
    assert categories["note.txt"] == "modified"
    assert categories["drop.txt"] == "deleted"
    assert report.execution.exit_code == 0
    assert report.session.name == "experiment-1"
    assert "not a security boundary" in report.security_notice.lower()

    reloaded = SessionStore(data).get("experiment-1")
    assert reloaded.executions[0].stdout == ""
    assert reloaded.status == "ready"
    assert (reloaded.workspace / "created.txt").read_text() == "new\n"


def test_failed_command_leaves_an_inspectable_workspace(tmp_path: Path) -> None:
    source = _source(tmp_path)
    report = run_once(
        source=source,
        name="fails",
        command=[sys.executable, "-c", "raise SystemExit(7)"],
        data_dir=tmp_path / "data",
        timeout_seconds=30,
    )
    assert report.execution.exit_code == 7
    assert report.session.workspace.is_dir()
    assert SessionStore(tmp_path / "data").get("fails").status == "failed"


def test_second_execution_is_refused_while_one_is_running(tmp_path: Path) -> None:
    source = _source(tmp_path)
    data = tmp_path / "data"
    created = create_session(source=source, name="held", data_dir=data)
    import threading
    import time

    def hold() -> None:
        exec_session(
            name="held",
            command=[
                sys.executable,
                "-c",
                "from pathlib import Path; Path('started').write_text('1'); "
                + "import time; time.sleep(30)",
            ],
            data_dir=data,
            timeout_seconds=30,
        )

    worker = threading.Thread(target=hold)
    worker.start()
    marker = created.workspace / "started"
    deadline = time.monotonic() + 10
    while not marker.exists():
        if not worker.is_alive() or time.monotonic() > deadline:
            worker.join(timeout=1)
            raise AssertionError("the holding command did not start")
        time.sleep(0.05)
    with pytest.raises(SessionBusy):
        exec_session(
            name="held",
            command=[sys.executable, "-c", "print('nope')"],
            data_dir=data,
            timeout_seconds=30,
        )
    destroy_session(name="held", data_dir=data, force=True)
    worker.join(timeout=10)
    assert not worker.is_alive()


def test_missing_session_is_not_found(tmp_path: Path) -> None:
    with pytest.raises(NotFoundError):
        SessionStore(tmp_path / "data").get("missing")


def test_symlink_outside_the_workspace_is_reported(tmp_path: Path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("nope\n")
    (source / "jump").symlink_to(outside)
    report = run_once(
        source=source,
        name="links",
        command=[sys.executable, "-c", "print('ok')"],
        data_dir=tmp_path / "data",
        timeout_seconds=30,
    )
    assert report.outside_links == ["jump"]

"""The text interface browses sessions through the same services as the CLI."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from lupaxa.sandbox.exceptions import UsageError
from lupaxa.sandbox.service import create_session
from lupaxa.sandbox.tui.launch import run_tui


def test_missing_textual_is_a_usage_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "textual", None)
    sys.modules.pop("lupaxa.sandbox.tui.app", None)
    with pytest.raises(UsageError, match="lupaxa-sandbox\\[tui\\]"):
        run_tui(tmp_path)


def test_session_browser_filters_changes_and_exports(tmp_path: Path) -> None:
    from textual.widgets import Input, OptionList, Static

    from lupaxa.sandbox.tui.app import SandboxApp

    project = tmp_path / "project"
    project.mkdir()
    (project / "note.txt").write_text("v1\n")
    data = tmp_path / "data"
    session = create_session(source=project, name="demo", data_dir=data)
    (session.workspace / "extra.txt").write_text("added\n")
    destination = tmp_path / "out"

    async def body() -> None:
        app = SandboxApp(data)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            sessions = app.screen.query_one("#sessions", OptionList)
            assert any("demo" in str(option.prompt) for option in sessions.options)
            await pilot.press("enter")
            await pilot.pause()
            changes = app.screen.query_one("#changes", OptionList)
            assert any("extra.txt" in str(option.prompt) for option in changes.options)
            app.screen.query_one("#filter", Input).focus()
            await pilot.press(*"zzz")
            await pilot.pause()
            changes = app.screen.query_one("#changes", OptionList)
            assert not any("extra.txt" in str(option.prompt) for option in changes.options)
            app.screen.query_one("#filter", Input).value = ""
            await pilot.pause()
            app.screen.query_one("#changes", OptionList).focus()
            await pilot.press("enter")
            await pilot.pause()
            diff = app.screen.query_one("#diff", Static)
            assert "added" in str(diff.content)
            await pilot.press("e")
            await pilot.pause()
            app.screen.query_one("#export-dest", Input).value = str(destination)
            await pilot.press("enter")
            await pilot.pause()
            assert (destination / "extra.txt").read_text() == "added\n"
            assert (session.workspace / "extra.txt").read_text() == "added\n"

    asyncio.run(body())


def test_apply_waits_for_confirmation_and_shows_conflicts(tmp_path: Path) -> None:
    from textual.widgets import Static

    from lupaxa.sandbox.tui.app import SandboxApp

    project = tmp_path / "project"
    project.mkdir()
    (project / "note.txt").write_text("v1\n")
    data = tmp_path / "data"
    session = create_session(source=project, name="demo", data_dir=data)
    (session.workspace / "note.txt").write_text("v2\n")
    (project / "note.txt").write_text("v3\n")

    async def body() -> None:
        app = SandboxApp(data)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            preview = str(app.screen.query_one("#apply-preview", Static).content)
            assert "conflict" in preview.lower()
            assert (project / "note.txt").read_text() == "v3\n"
            await pilot.press("c")
            await pilot.pause()
            assert (project / "note.txt").read_text() == "v3\n"

    asyncio.run(body())

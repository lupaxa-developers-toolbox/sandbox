"""CLI help, JSON output, and exit codes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lupaxa.sandbox.cli import app, main
from lupaxa.sandbox.version import __version__

runner = CliRunner()


def test_help_mentions_the_security_limitation() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "not a security boundary" in result.stdout.lower()


def test_unknown_option_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--doctor"])
    captured = capsys.readouterr()
    assert code == 2
    assert "No such option: --doctor" in captured.err
    assert "sandbox doctor" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_unknown_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["nope"])
    captured = capsys.readouterr()
    assert code == 2
    assert "No such command" in captured.err
    assert "Traceback" not in captured.err


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_run_json_reports_a_created_file(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "note.txt").write_text("one\n")
    monkeypatch.setenv("LUPAXA_SANDBOX_DATA", str(tmp_path / "data"))
    result = runner.invoke(
        app,
        [
            "run",
            "--source",
            str(source),
            "--name",
            "json-run",
            "--json",
            "--",
            sys.executable,
            "-c",
            "open('note.txt','w').write('two\\n')",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["execution"]["exit_code"] == 0
    assert payload["changes"][0]["path"] == "note.txt"
    assert payload["changes"][0]["category"] == "modified"
    assert payload["capabilities"]["filesystem_restrictions"] is False
    assert "security_notice" in payload


def test_timeout_exit_code(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "project"
    source.mkdir()
    monkeypatch.setenv("LUPAXA_SANDBOX_DATA", str(tmp_path / "data"))
    result = runner.invoke(
        app,
        [
            "run",
            "--source",
            str(source),
            "--name",
            "slow",
            "--timeout",
            "1",
            "--json",
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    payload = json.loads(result.stdout)
    assert payload["execution"]["termination_reason"] == "timeout"
    assert result.exit_code == 5


def test_diff_stat_and_missing_session(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "note.txt").write_text("one\n")
    monkeypatch.setenv("LUPAXA_SANDBOX_DATA", str(tmp_path / "data"))
    created = runner.invoke(
        app,
        [
            "run",
            "--source",
            str(source),
            "--name",
            "keep",
            "--quiet",
            "--",
            sys.executable,
            "-c",
            "open('note.txt','w').write('two\\n')",
        ],
    )
    assert created.exit_code == 0, created.stdout
    diff = runner.invoke(app, ["diff", "keep", "--stat", "--json"])
    body = json.loads(diff.stdout)
    assert body["summary"]["modified"] == 1
    missing = runner.invoke(app, ["show", "nope"])
    assert missing.exit_code == 3
    shown = runner.invoke(app, ["diff", "keep"])
    assert "-one" in shown.stdout
    assert "+two" in shown.stdout

"""Unified diffs stay textual only when the bytes are safe to decode."""

from __future__ import annotations

from lupaxa.sandbox.workspace.textdiff import file_diff


def test_text_diff_is_unified() -> None:
    diff = file_diff("src/version.py", b'__version__ = "0.9.0"\n', b'__version__ = "1.0.0"\n')
    assert diff.binary is False
    assert '-__version__ = "0.9.0"' in diff.text
    assert '+__version__ = "1.0.0"' in diff.text


def test_nul_and_invalid_utf8_are_binary() -> None:
    nul = file_diff("a.bin", b"a\0b", b"a\0c")
    bad = file_diff("b.bin", b"\xff\xfe", b"ok")
    assert nul.binary is True
    assert nul.text == ""
    assert bad.binary is True


def test_large_file_is_skipped_but_not_decoded() -> None:
    blob = b"x" * 32
    diff = file_diff("big.txt", blob, blob + b"y", max_bytes=16)
    assert diff.skipped is True
    assert diff.binary is False
    assert diff.text == ""

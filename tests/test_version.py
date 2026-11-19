"""Version shape only. Never a hard-coded release number."""

from __future__ import annotations

import re

from lupaxa.sandbox import __version__, get_version


def test_version_is_dotted_semver() -> None:
    assert get_version() == __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?", __version__)

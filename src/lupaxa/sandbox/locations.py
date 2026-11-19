"""Platform data directories."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "lupaxa-sandbox"


def data_dir(explicit: Path | None = None) -> Path:
    """Return the session store directory.

    An explicit path wins, then ``LUPAXA_SANDBOX_DATA``, then the platform
    data directory from ``platformdirs``.
    """
    if explicit is not None:
        return Path(explicit)
    override = os.environ.get("LUPAXA_SANDBOX_DATA")
    if override:
        return Path(override)
    return Path(user_data_dir(APP_NAME, appauthor=False))

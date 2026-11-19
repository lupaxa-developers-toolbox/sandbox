"""Start the text interface, or explain how to install it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lupaxa.sandbox.exceptions import UsageError


def load_app() -> Any:
    """Import the Textual app. Missing Textual raises ``ImportError``."""
    from lupaxa.sandbox.tui.app import SandboxApp

    return SandboxApp


def run_tui(data_dir: Path) -> None:
    """Run the session browser. A normal install does not include this interface."""
    try:
        app_cls = load_app()
    except ImportError as exc:
        raise UsageError(
            "The text interface is optional. Install it with: pip install 'lupaxa-sandbox[tui]'"
        ) from exc
    app_cls(data_dir).run()

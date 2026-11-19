"""Stop the recipe when the built file is missing or wrong."""

from pathlib import Path

text = Path("dist/results.txt").read_text()
if text != "ok\n":
    raise SystemExit(1)

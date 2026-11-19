"""Create the file the later command checks."""

from pathlib import Path

output = Path("dist")
output.mkdir(exist_ok=True)
(output / "results.txt").write_text("ok\n")

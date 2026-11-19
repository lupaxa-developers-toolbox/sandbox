"""Write one new file. The copied note.txt is left as it was."""

from pathlib import Path

Path("generated.txt").write_text("ok\n")

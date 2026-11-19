"""Record the interpreter that actually ran this command."""

import sys
from pathlib import Path

Path("python-version.txt").write_text(sys.version + "\n")

"""Run Import Linter without depending on its console script being on PATH."""

import sys
from pathlib import Path

from importlinter.cli import lint_imports

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    raise SystemExit(lint_imports())

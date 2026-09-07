"""Use the existing Node approach; native dialog behavior needs browser verification."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_side_panel_controller():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable; use the documented manual browser checks")
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [
            node,
            str(root / "tests/javascript/side_panels.cjs"),
            str(root / "src/refrncr/static/js/refrncr.js"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

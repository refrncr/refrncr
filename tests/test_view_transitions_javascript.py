"""Test view transition lifecycle using the repository's dependency-free Node harness."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_view_transition_controller():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable; use the documented manual browser checks")
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [
            node,
            str(root / "tests/javascript/view_transitions.cjs"),
            str(root / "src/refrncr/static/js/view-transitions.js"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

"""Exercise the generated-key controller with the existing Node test convention."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_key_generation_interaction():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable")
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [
            node,
            str(root / "tests/javascript/key_generation.cjs"),
            str(root / "src/refrncr/static/js/key-generation.js"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

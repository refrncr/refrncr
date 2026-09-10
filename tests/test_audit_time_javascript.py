"""Fast timezone regressions; native controls/dialogs have a separate browser script."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_local_audit_time():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [
            node,
            str(root / "tests/javascript/audit_time.cjs"),
            str(root / "src/refrncr/static/js/audit-time.js"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

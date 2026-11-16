"""Import-order guard for every production module."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
PACKAGE_ROOT = SOURCE_ROOT / "refrncr"


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SOURCE_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


MODULES = tuple(
    _module_name(path)
    for path in sorted(PACKAGE_ROOT.rglob("*.py"))
    if path.relative_to(PACKAGE_ROOT).parts[:2] != ("migrations", "versions")
    and path != PACKAGE_ROOT / "migrations" / "env.py"
)


def _fresh_python(source: str) -> subprocess.CompletedProcess[str]:
    python_path = os.pathsep.join(
        part for part in (str(SOURCE_ROOT), os.environ.get("PYTHONPATH")) if part
    )
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": python_path},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_in_fresh_python(module):
    result = _fresh_python(f"import {module}")

    assert result.returncode == 0, result.stderr


def test_migration_environment_models_first_import():
    """Mirror the first application import made by migrations/env.py."""
    result = _fresh_python("from refrncr.persistence import models")

    assert result.returncode == 0, result.stderr

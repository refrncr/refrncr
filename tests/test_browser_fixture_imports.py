"""Import contracts shared by disposable browser-test applications."""

import ast
from pathlib import Path
from types import ModuleType

import refrncr.web as web

ROOT = Path(__file__).resolve().parents[1]


def _missing_web_imports(module: ModuleType) -> dict[str, set[str]]:
    missing = {}
    for path in sorted((ROOT / "tests").glob("browser_*_app.py")):
        tree = ast.parse(path.read_text())
        names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "refrncr.web"
            for alias in node.names
        }
        unavailable = {name for name in names if not hasattr(module, name)}
        if unavailable:
            missing[path.name] = unavailable
    return missing


def test_browser_fixture_web_imports_are_available_without_executing_fixtures():
    assert _missing_web_imports(web) == {}


def test_browser_fixture_import_guard_detects_missing_export(monkeypatch):
    monkeypatch.delattr(web, "public_route")

    assert _missing_web_imports(web) == {"browser_select_app.py": {"public_route"}}

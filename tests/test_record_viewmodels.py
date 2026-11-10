import ast
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from refrncr.web.viewmodels.records import record_value_formatter

FORBIDDEN_VIEWMODEL_IMPORTS = {"flask", "sqlalchemy", "werkzeug"}


def assert_viewmodels_are_framework_independent(root: Path) -> None:
    violations = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
                imported = {name.split(".", 1)[0] for name in names}
                forbidden_web = {
                    name
                    for name in names
                    if name.startswith("refrncr.web")
                    and not name.startswith("refrncr.web.viewmodels")
                }
            elif isinstance(node, ast.ImportFrom):
                imported = {node.module.split(".", 1)[0]} if node.module else set()
                imported_paths = {
                    ".".join(part for part in (node.module, alias.name) if part)
                    for alias in node.names
                }
                forbidden_web = {
                    name
                    for name in imported_paths
                    if (name == "refrncr.web" or name.startswith("refrncr.web."))
                    and name != "refrncr.web.viewmodels"
                    and not name.startswith("refrncr.web.viewmodels.")
                }
                if node.level >= 2:
                    forbidden_web.add(node.module or next(iter(imported_paths)))
            else:
                continue
            violations.extend(
                f"{path.name}:{node.lineno}: {name}"
                for name in sorted(imported & FORBIDDEN_VIEWMODEL_IMPORTS)
            )
            violations.extend(
                f"{path.name}:{node.lineno}: {name}" for name in sorted(forbidden_web)
            )
    assert not violations, "View-model framework imports:\n" + "\n".join(violations)


def field(key, data_type="number"):
    return SimpleNamespace(key=key, data_type=data_type)


def test_viewmodel_modules_have_no_web_or_database_framework_imports():
    root = Path(__file__).resolve().parents[1] / "src/refrncr/web/viewmodels"

    assert_viewmodels_are_framework_independent(root)


def test_viewmodel_import_boundary_rejects_a_forbidden_import(tmp_path):
    (tmp_path / "future_viewmodel.py").write_text("from flask import request\n")

    with pytest.raises(AssertionError, match=r"future_viewmodel\.py:1: flask"):
        assert_viewmodels_are_framework_independent(tmp_path)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from refrncr.web.routing import version_errors\n", "refrncr.web.routing"),
        ("from ..routing import version_errors\n", "routing"),
        ("from refrncr.web import routing\n", "refrncr.web.routing"),
        ("import refrncr.web.routing\n", "refrncr.web.routing"),
        ("from .. import routing\n", "routing"),
        ("from refrncr import web\n", "refrncr.web"),
    ],
)
def test_viewmodel_import_boundary_rejects_web_layer_imports(tmp_path, source, expected):
    (tmp_path / "future_viewmodel.py").write_text(source)

    with pytest.raises(AssertionError, match=rf"future_viewmodel\.py:1: {expected}"):
        assert_viewmodels_are_framework_independent(tmp_path)


def test_viewmodel_import_boundary_allows_viewmodels_and_domain_modules(tmp_path):
    (tmp_path / "future_viewmodel.py").write_text(
        "from . import records\n"
        "from refrncr.web.viewmodels.records import record_value_formatter\n"
        "from refrncr.datatypes import get_datatype\n"
    )

    assert_viewmodels_are_framework_independent(tmp_path)


def test_decimal_columns_use_the_largest_visible_scale():
    amount = field("amount")
    display = record_value_formatter(
        (amount,),
        ({"amount": Decimal("1.2")}, {"amount": Decimal("3.450")}),
    )

    assert display(amount, Decimal("1.2")) == "1.200"
    assert display(amount, Decimal("3.450")) == "3.450"


def test_decimal_scale_never_becomes_negative_for_exponential_values():
    amount = field("amount")
    display = record_value_formatter((amount,), ({"amount": Decimal("1E+3")},))

    assert display(amount, Decimal("1E+3")) == "1000"


def test_missing_numeric_values_remain_missing():
    amount = field("amount")
    display = record_value_formatter((amount,), ({}, {"amount": None}))

    assert display(amount, None) is None


def test_columns_calculate_scales_independently_and_ignore_non_decimals():
    amount = field("amount")
    ratio = field("ratio")
    display = record_value_formatter(
        (amount, ratio),
        (
            {"amount": Decimal("1.20"), "ratio": "not parsed"},
            {"amount": 3, "ratio": Decimal("4.567")},
        ),
    )

    assert display(amount, Decimal("8")) == "8.00"
    assert display(ratio, Decimal("9.1")) == "9.100"


def test_non_numeric_values_keep_the_existing_datatype_formatting():
    label = field("label", "string")
    display = record_value_formatter((label,), ({"label": "Café"},))

    assert display(label, "Café") == "Café"
    assert display(label, None) is None

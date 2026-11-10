"""Hierarchy blueprint and typed application context."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flask import Flask, current_app
from werkzeug.local import LocalProxy

from refrncr.dataset_versions import DatasetVersionService
from refrncr.hierarchy import HierarchyService
from refrncr.hierarchy_import import HierarchyImportService
from refrncr.services import AuthorizationService, DatasetRecordService
from refrncr.web.routing import DatasetBlueprint

blueprint = DatasetBlueprint("hierarchies", __name__)


@dataclass(frozen=True)
class HierarchiesContext:
    authorization: AuthorizationService
    dataset_versions: DatasetVersionService
    hierarchy: HierarchyService
    hierarchy_imports: HierarchyImportService | None
    dataset_records: DatasetRecordService
    require_authenticated: Callable[[], Any]
    require_csrf: Callable[[str | None], None]
    render_authenticated_template: Callable[..., Any]
    exact_expected_revision: Callable[[], int]


def _context() -> HierarchiesContext:
    return current_app.extensions["refrncr.hierarchies"]


authorization = LocalProxy(lambda: _context().authorization)
dataset_versions = LocalProxy(lambda: _context().dataset_versions)
hierarchy = LocalProxy(lambda: _context().hierarchy)
dataset_records = LocalProxy(lambda: _context().dataset_records)


def require_authenticated():
    return _context().require_authenticated()


def require_csrf(expected_token: str | None) -> None:
    _context().require_csrf(expected_token)


def hierarchy_import_service() -> HierarchyImportService | None:
    return _context().hierarchy_imports


def render_authenticated_template(template_name: str, **context):
    return _context().render_authenticated_template(template_name, **context)


def exact_expected_revision() -> int:
    return _context().exact_expected_revision()


from refrncr.web.hierarchies import imports as _imports  # noqa: E402, F401
from refrncr.web.hierarchies import maintenance as _maintenance  # noqa: E402, F401
from refrncr.web.hierarchies import views as _views  # noqa: E402, F401


def register_hierarchies_blueprint(
    app: Flask,
    *,
    authorization: AuthorizationService | None,
    dataset_versions: DatasetVersionService | None,
    hierarchy: HierarchyService | None,
    hierarchy_imports: HierarchyImportService | None,
    dataset_records: DatasetRecordService | None,
    require_authenticated: Callable[[], Any],
    require_csrf: Callable[[str | None], None],
    render_authenticated_template: Callable[..., Any],
    exact_expected_revision: Callable[[], int],
) -> None:
    if (
        authorization is not None
        and dataset_versions is not None
        and hierarchy is not None
        and dataset_records is not None
    ):
        app.extensions["refrncr.hierarchies"] = HierarchiesContext(
            authorization=authorization,
            dataset_versions=dataset_versions,
            hierarchy=hierarchy,
            hierarchy_imports=hierarchy_imports,
            dataset_records=dataset_records,
            require_authenticated=require_authenticated,
            require_csrf=require_csrf,
            render_authenticated_template=render_authenticated_template,
            exact_expected_revision=exact_expected_revision,
        )
    app.register_blueprint(blueprint)

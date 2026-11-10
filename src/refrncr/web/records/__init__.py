"""Record browsing, maintenance, and export blueprint."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flask import Flask, current_app
from werkzeug.local import LocalProxy

from refrncr.dataset_versions import DatasetVersionService
from refrncr.record_import import DatasetRecordImportService
from refrncr.services import (
    AuthorizationService,
    DatasetBusinessKeyService,
    DatasetFieldService,
    DatasetRecordService,
    DatasetService,
)
from refrncr.web.routing import DatasetBlueprint

blueprint = DatasetBlueprint("records", __name__)


@dataclass(frozen=True)
class RecordsContext:
    authorization: AuthorizationService
    datasets: DatasetService
    dataset_fields: DatasetFieldService
    dataset_business_keys: DatasetBusinessKeyService
    dataset_records: DatasetRecordService
    dataset_versions: DatasetVersionService
    record_imports: DatasetRecordImportService
    require_authenticated: Callable[[], Any]
    require_csrf: Callable[[str | None], None]
    render_authenticated_template: Callable[..., Any]
    render_mapping_template: Callable[..., Any]
    render_hierarchy: Callable[[Any, str], Any]
    active_navigation: Callable[[], str]
    exact_expected_revision: Callable[[], int]


def _context() -> RecordsContext:
    return current_app.extensions["refrncr.records"]


authorization = LocalProxy(lambda: _context().authorization)
datasets = LocalProxy(lambda: _context().datasets)
dataset_fields = LocalProxy(lambda: _context().dataset_fields)
dataset_business_keys = LocalProxy(lambda: _context().dataset_business_keys)
dataset_records = LocalProxy(lambda: _context().dataset_records)
dataset_versions = LocalProxy(lambda: _context().dataset_versions)
record_imports = LocalProxy(lambda: _context().record_imports)


def require_authenticated():
    return _context().require_authenticated()


def require_csrf(expected_token: str | None) -> None:
    _context().require_csrf(expected_token)


def require_dataset_dependencies():
    context = _context()
    return (
        context.authorization,
        context.datasets,
        context.dataset_fields,
        context.dataset_business_keys,
        context.dataset_records,
    )


def render_authenticated_template(template_name: str, **context):
    return _context().render_authenticated_template(template_name, **context)


def render_mapping_template(template_name: str, **context):
    return _context().render_mapping_template(template_name, **context)


def render_hierarchy(view, actor_id: str):
    return _context().render_hierarchy(view, actor_id)


def active_navigation() -> str:
    return _context().active_navigation()


def exact_expected_revision() -> int:
    return _context().exact_expected_revision()


from refrncr.web.records import browse as _browse  # noqa: E402, F401
from refrncr.web.records import maintenance as _maintenance  # noqa: E402, F401


def register_records_blueprint(
    app: Flask,
    *,
    authorization: AuthorizationService | None,
    datasets: DatasetService | None,
    dataset_fields: DatasetFieldService | None,
    dataset_business_keys: DatasetBusinessKeyService | None,
    dataset_records: DatasetRecordService | None,
    dataset_versions: DatasetVersionService | None,
    record_imports: DatasetRecordImportService | None,
    require_authenticated: Callable[[], Any],
    require_csrf: Callable[[str | None], None],
    render_authenticated_template: Callable[..., Any],
    render_mapping_template: Callable[..., Any],
    render_hierarchy: Callable[[Any, str], Any],
    active_navigation: Callable[[], str],
    exact_expected_revision: Callable[[], int],
) -> None:
    dependencies = (
        authorization,
        datasets,
        dataset_fields,
        dataset_business_keys,
        dataset_records,
        dataset_versions,
        record_imports,
    )
    if all(dependency is not None for dependency in dependencies):
        app.extensions["refrncr.records"] = RecordsContext(
            authorization=authorization,
            datasets=datasets,
            dataset_fields=dataset_fields,
            dataset_business_keys=dataset_business_keys,
            dataset_records=dataset_records,
            dataset_versions=dataset_versions,
            record_imports=record_imports,
            require_authenticated=require_authenticated,
            require_csrf=require_csrf,
            render_authenticated_template=render_authenticated_template,
            render_mapping_template=render_mapping_template,
            render_hierarchy=render_hierarchy,
            active_navigation=active_navigation,
            exact_expected_revision=exact_expected_revision,
        )
    app.register_blueprint(blueprint)

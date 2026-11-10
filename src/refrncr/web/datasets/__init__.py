"""Dataset browser blueprint and its typed application context."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flask import Flask, current_app
from werkzeug.local import LocalProxy

from refrncr.dataset_versions import DatasetVersionService
from refrncr.services import (
    AuthorizationService,
    DatasetBusinessKeyService,
    DatasetFieldService,
    DatasetRecordService,
    DatasetService,
)
from refrncr.web.routing import DatasetBlueprint

blueprint = DatasetBlueprint("datasets", __name__)


@dataclass(frozen=True)
class DatasetsContext:
    authorization: AuthorizationService
    datasets: DatasetService
    dataset_fields: DatasetFieldService
    dataset_business_keys: DatasetBusinessKeyService
    dataset_records: DatasetRecordService
    dataset_versions: DatasetVersionService
    require_authenticated: Callable[[], Any]
    require_csrf: Callable[[str | None], None]
    render_authenticated_template: Callable[..., Any]
    render_mapping_template: Callable[..., Any]
    active_navigation: Callable[[], str]
    audit_security: Callable[..., None]


def _context() -> DatasetsContext:
    return current_app.extensions["refrncr.datasets"]


authorization = LocalProxy(lambda: _context().authorization)
datasets = LocalProxy(lambda: _context().datasets)
dataset_fields = LocalProxy(lambda: _context().dataset_fields)
dataset_business_keys = LocalProxy(lambda: _context().dataset_business_keys)
dataset_records = LocalProxy(lambda: _context().dataset_records)
dataset_versions = LocalProxy(lambda: _context().dataset_versions)


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


def active_navigation() -> str:
    return _context().active_navigation()


def audit_security(action: str, *, actor_user_id: str | None = None) -> None:
    _context().audit_security(action, actor_user_id=actor_user_id)


def register_datasets_blueprint(
    app: Flask,
    *,
    authorization: AuthorizationService | None,
    datasets: DatasetService | None,
    dataset_fields: DatasetFieldService | None,
    dataset_business_keys: DatasetBusinessKeyService | None,
    dataset_records: DatasetRecordService | None,
    dataset_versions: DatasetVersionService | None,
    require_authenticated: Callable[[], Any],
    require_csrf: Callable[[str | None], None],
    render_authenticated_template: Callable[..., Any],
    render_mapping_template: Callable[..., Any],
    active_navigation: Callable[[], str],
    audit_security: Callable[..., None],
) -> None:
    dependencies = (
        authorization,
        datasets,
        dataset_fields,
        dataset_business_keys,
        dataset_records,
        dataset_versions,
    )
    if all(dependency is not None for dependency in dependencies):
        app.extensions["refrncr.datasets"] = DatasetsContext(
            authorization=authorization,
            datasets=datasets,
            dataset_fields=dataset_fields,
            dataset_business_keys=dataset_business_keys,
            dataset_records=dataset_records,
            dataset_versions=dataset_versions,
            require_authenticated=require_authenticated,
            require_csrf=require_csrf,
            render_authenticated_template=render_authenticated_template,
            render_mapping_template=render_mapping_template,
            active_navigation=active_navigation,
            audit_security=audit_security,
        )
    app.register_blueprint(blueprint)


from refrncr.web.datasets import access as access  # noqa: E402
from refrncr.web.datasets import core as core  # noqa: E402
from refrncr.web.datasets import maintenance as maintenance  # noqa: E402

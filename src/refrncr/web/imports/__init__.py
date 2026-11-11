"""Record-import blueprint and typed application context."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flask import Flask, current_app
from werkzeug.local import LocalProxy

from refrncr.record_import import DatasetRecordImportService
from refrncr.services import DatasetRecordService
from refrncr.web.routing import DatasetBlueprint

blueprint = DatasetBlueprint("imports", __name__)


@dataclass(frozen=True)
class ImportsContext:
    dataset_records: DatasetRecordService
    record_imports: DatasetRecordImportService
    require_authenticated: Callable[[], Any]
    require_csrf: Callable[[str | None], None]
    require_dataset_dependencies: Callable[[], Any]
    render_authenticated_template: Callable[..., Any]
    audit_security: Callable[..., None]


def _context() -> ImportsContext:
    return current_app.extensions["refrncr.imports"]


dataset_records = LocalProxy(lambda: _context().dataset_records)
record_imports = LocalProxy(lambda: _context().record_imports)


def require_authenticated():
    return _context().require_authenticated()


def require_csrf(expected_token: str | None) -> None:
    _context().require_csrf(expected_token)


def require_dataset_dependencies():
    return _context().require_dataset_dependencies()


def render_authenticated_template(template_name: str, **context):
    return _context().render_authenticated_template(template_name, **context)


def audit_security(event: str, **details) -> None:
    _context().audit_security(event, **details)


from refrncr.web.imports import routes as _routes  # noqa: E402, F401


def register_imports_blueprint(
    app: Flask,
    *,
    dataset_records: DatasetRecordService | None,
    record_imports: DatasetRecordImportService | None,
    require_authenticated: Callable[[], Any],
    require_csrf: Callable[[str | None], None],
    require_dataset_dependencies: Callable[[], Any],
    render_authenticated_template: Callable[..., Any],
    audit_security: Callable[..., None],
) -> None:
    if dataset_records is not None and record_imports is not None:
        app.extensions["refrncr.imports"] = ImportsContext(
            dataset_records=dataset_records,
            record_imports=record_imports,
            require_authenticated=require_authenticated,
            require_csrf=require_csrf,
            require_dataset_dependencies=require_dataset_dependencies,
            render_authenticated_template=render_authenticated_template,
            audit_security=audit_security,
        )
    app.register_blueprint(blueprint)

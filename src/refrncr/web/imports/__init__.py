"""Record-import blueprint and typed application context."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any

from flask import Flask, current_app, g, url_for
from werkzeug.local import LocalProxy

from refrncr.record_import import (
    DatasetRecordImportService,
    ImportExpiredError,
    ImportValidationError,
)
from refrncr.services import (
    AuthorizationError,
    ConflictError,
    DatasetRecordService,
    NotFoundError,
)
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


def import_route(rule, *, capability: str, **options):
    def decorate(function):
        function.required_capability = capability
        if getattr(function, "_secured_import", False):
            return blueprint.dataset_route(rule, capability=capability, **options)(function)

        @wraps(function)
        def secured_import(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except AuthorizationError:
                resolved = getattr(g, "authenticated_resolution", None)
                audit_security(
                    "security.authorization_denied",
                    actor_user_id=resolved[0].id if resolved else None,
                )
                return render_authenticated_template(
                    "pages/error.html",
                    error_code=403,
                    error_message="The import is not available to this account.",
                ), 403
            except ImportExpiredError:
                resolved = getattr(g, "authenticated_resolution", None)
                if resolved is not None:
                    audit_security("security.workflow_token_rejected", actor_user_id=resolved[0].id)
                minutes = max(1, (record_imports.store.ttl + 59) // 60)
                return render_authenticated_template(
                    "pages/datasets/import/unavailable.html",
                    error=(
                        f"This import expired after {minutes} minutes without activity. "
                        "Upload the file again to continue."
                    ),
                    return_url=url_for("imports.import_dataset_records", key=kwargs.get("key", "")),
                ), 410
            except NotFoundError:
                resolved = getattr(g, "authenticated_resolution", None)
                if resolved is not None:
                    audit_security("security.workflow_token_rejected", actor_user_id=resolved[0].id)
                return render_authenticated_template(
                    "pages/error.html",
                    error_code=404,
                    error_message=(
                        "The import has expired or is unavailable. Upload the file again."
                    ),
                ), 404
            except ConflictError as error:
                return render_authenticated_template(
                    "pages/datasets/import/unavailable.html",
                    error=str(error),
                    return_url=url_for("imports.import_dataset_records", key=kwargs.get("key", "")),
                ), 409
            except ImportValidationError as error:
                return render_authenticated_template(
                    "pages/datasets/import/unavailable.html", error=str(error)
                ), 400

        secured_import._secured_import = True
        return blueprint.dataset_route(rule, capability=capability, **options)(secured_import)

    return decorate


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

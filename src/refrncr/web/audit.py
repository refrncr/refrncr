"""Audit-log browser blueprint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from flask import Blueprint, Flask, abort, current_app, request, url_for

from refrncr.audit_history import AuditHistoryService, filter_values, parse_audit_filters
from refrncr.audit_presentation import ACTION_LABELS, OBJECTS
from refrncr.ports import AuditReadUnavailable
from refrncr.services import AuthorizationError, NotFoundError, ValidationError
from refrncr.web.routing import capability_route, no_store_route

blueprint = Blueprint("audit", __name__)


class CapabilityResolver(Protocol):
    def __call__(self, capability: str) -> tuple[Any, Any]: ...


class AuthenticatedRenderer(Protocol):
    def __call__(self, template_name: str, **context: Any) -> str: ...


@dataclass(frozen=True)
class AuditContext:
    audit_history: AuditHistoryService
    require_capability: CapabilityResolver
    render_authenticated_template: AuthenticatedRenderer


def _context() -> AuditContext:
    return current_app.extensions["refrncr.audit"]


def _audit_error(message: str, status: int = 400):
    return _context().render_authenticated_template("pages/audit/error.html", error=message), status


@blueprint.get("/settings/audit-log")
@capability_route("can_administer_installation")
@no_store_route
def audit_list():
    context = _context()
    actor, _session = context.require_capability("can_administer_installation")
    try:
        if len(request.query_string) > 4096:
            raise ValidationError("The audit filter URL is too long. Clear filters to start again.")
        filters, cursor = parse_audit_filters(request.args.items(multi=True))
        page = context.audit_history.list_history(actor.id, filters, cursor)
    except AuthorizationError:
        abort(403)
    except ValidationError as error:
        return _audit_error(str(error))
    except AuditReadUnavailable:
        return _audit_error("Audit history is temporarily unavailable. Try again later.", 503)
    values = filter_values(filters)
    return context.render_authenticated_template(
        "pages/audit/list.html",
        page=page,
        filters=values,
        next_url=url_for("audit.audit_list", **values, cursor=page.next_cursor)
        if page.next_cursor
        else None,
        previous_url=url_for("audit.audit_list", **values, cursor=page.previous_cursor)
        if page.previous_cursor
        else None,
        actions=sorted(
            (f"{kind}:{action}", label) for (kind, action), label in ACTION_LABELS.items()
        ),
        object_types=sorted((key, value.label) for key, value in OBJECTS.items()),
    )


@blueprint.get("/settings/audit-log/<event_id>")
@capability_route("can_administer_installation")
@no_store_route
def audit_detail(event_id: str):
    context = _context()
    actor, _session = context.require_capability("can_administer_installation")
    try:
        item = context.audit_history.get_event(actor.id, event_id)
        if request.args:
            raise ValidationError("This audit detail link contains unsupported parameters.")
    except AuthorizationError:
        abort(403)
    except NotFoundError:
        abort(404)
    except ValidationError as error:
        return _audit_error(str(error))
    except AuditReadUnavailable:
        return _audit_error("Audit history is temporarily unavailable. Try again later.", 503)
    return context.render_authenticated_template("pages/audit/detail.html", item=item)


def register_audit_blueprint(
    app: Flask,
    *,
    audit_history: AuditHistoryService,
    require_capability: CapabilityResolver,
    render_authenticated_template: AuthenticatedRenderer,
) -> None:
    app.extensions["refrncr.audit"] = AuditContext(
        audit_history=audit_history,
        require_capability=require_capability,
        render_authenticated_template=render_authenticated_template,
    )
    app.register_blueprint(blueprint)

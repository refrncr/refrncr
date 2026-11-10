"""Overview browser blueprint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from flask import Flask, abort, current_app, render_template, request, url_for

from refrncr.overview import REASON_LABELS, OverviewService
from refrncr.web.routing import (
    DatasetBlueprint,
    authenticated_route,
    no_store_route,
    version_errors,
)

blueprint = DatasetBlueprint("overview", __name__)


class AuthenticationResolver(Protocol):
    def __call__(self) -> tuple[Any, Any] | None: ...


class RequiredAuthenticationResolver(Protocol):
    def __call__(self) -> tuple[Any, Any]: ...


class AuthenticatedRenderer(Protocol):
    def __call__(self, template_name: str, **context: Any) -> Any: ...


class SecurityAuditor(Protocol):
    def __call__(self, action: str, *, actor_user_id: str | None = None) -> None: ...


@dataclass(frozen=True)
class OverviewContext:
    authenticated: AuthenticationResolver
    require_authenticated: RequiredAuthenticationResolver
    overview_service: OverviewService | None
    render_authenticated_template: AuthenticatedRenderer
    audit_security: SecurityAuditor
    dataset_ui_enabled: bool


def _context() -> OverviewContext:
    return current_app.extensions["refrncr.overview"]


def _render_overview(actor, *, full=False):
    context = _context()
    if context.overview_service is None:
        abort(404)
    owners = request.args.getlist("all_owners")
    reasons = request.args.getlist("reason")
    if len(owners) > 1 or (owners and owners[0] != "1"):
        abort(400)
    if len(reasons) > 1 or (reasons and reasons[0] not in {"", *(str(n) for n in REASON_LABELS)}):
        abort(400)
    reason = int(reasons[0]) if reasons and reasons[0] and full else None
    overview = context.overview_service.read(actor.id, all_owners=bool(owners))
    cards = [
        card
        for card in overview.attention
        if reason is None or any(item.number == reason for item in card.reasons)
    ]

    def destination(card, selected=None):
        draft = selected.draft if selected else card.version is None and card.has_draft
        return url_for(
            "records.dataset_records_view",
            key=card.dataset.key,
            concept=True if draft else None,
            version_id=None
            if draft
            else selected.version_id
            if selected
            else card.version.id
            if card.version
            else None,
            mapping_status=selected.mapping_status if selected else None,
        )

    return context.render_authenticated_template(
        "pages/overview.html",
        overview=overview,
        attention_cards=cards,
        full=full,
        reason=reason,
        reason_labels=REASON_LABELS,
        card_destination=destination,
        active_navigation="home",
    )


@blueprint.get("/")
@no_store_route
@authenticated_route
@version_errors
def index():
    context = _context()
    resolved = context.authenticated()
    actor = resolved[0] if resolved else None
    csrf = resolved[1].csrf_token if resolved else None
    if resolved and context.dataset_ui_enabled:
        return _render_overview(actor)
    return render_template(
        "pages/index.html",
        resolved=resolved,
        current_actor=actor,
        current_csrf=csrf,
        active_navigation="home",
        dataset_ui_enabled=context.dataset_ui_enabled,
    )


@blueprint.dataset_route("/attention", capability="can_access_community", methods=["GET"])
@no_store_route
@version_errors
def attention_overview():
    context = _context()
    actor, _session = context.require_authenticated()
    return _render_overview(actor, full=True)


def register_overview_blueprint(
    app: Flask,
    *,
    authenticated: AuthenticationResolver,
    require_authenticated: RequiredAuthenticationResolver,
    overview_service: OverviewService | None,
    render_authenticated_template: AuthenticatedRenderer,
    audit_security: SecurityAuditor,
    dataset_ui_enabled: bool,
) -> None:
    app.extensions["refrncr.overview"] = OverviewContext(
        authenticated=authenticated,
        require_authenticated=require_authenticated,
        overview_service=overview_service,
        render_authenticated_template=render_authenticated_template,
        audit_security=audit_security,
        dataset_ui_enabled=dataset_ui_enabled,
    )
    app.register_blueprint(blueprint)

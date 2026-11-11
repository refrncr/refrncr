"""Installation and account settings browser blueprint."""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import uuid4

from flask import Blueprint, Flask, abort, current_app, redirect, request, url_for

from refrncr.api_keys import LIFETIME, ApiKeyService
from refrncr.clock import Clock
from refrncr.installation_policy import EXPORT_POLICIES
from refrncr.installation_settings import InstallationSettingsService, PolicyConfirmationRequired
from refrncr.ports import PersistenceConflictError
from refrncr.services import (
    ConflictError,
    NotFoundError,
    UserService,
    ValidationError,
    parse_expected_revision,
)
from refrncr.web.routing import authenticated_route, capability_route, no_store_route

blueprint = Blueprint("settings", __name__)


class CapabilityResolver(Protocol):
    def __call__(self, capability: str) -> tuple[Any, Any]: ...


@dataclass(frozen=True)
class SettingsContext:
    users: UserService
    api_keys: ApiKeyService | None
    installation_settings: InstallationSettingsService | None
    clock: Clock
    review_secret: bytes
    require_authenticated: Callable[[], tuple[Any, Any]]
    require_capability: CapabilityResolver
    require_csrf: Callable[[str | None], None]
    require_dataset_dependencies: Callable[[], Any]
    render_authenticated_template: Callable[..., Any]


def _context() -> SettingsContext:
    return current_app.extensions["refrncr.settings"]


def _api_key_page(template: str = "list", **context: Any):
    return _context().render_authenticated_template(
        "pages/api_keys/" + template + ".html", **context
    )


def _require_api_keys() -> ApiKeyService:
    service = _context().api_keys
    if service is None:
        abort(503, description="API keys require server configuration")
    return service


@blueprint.get("/settings/api-keys")
@authenticated_route
@no_store_route
def api_key_list():
    context = _context()
    actor, _session = context.require_authenticated()
    if context.api_keys is None:
        return _api_key_page(keys=(), unavailable=True), 503
    return _api_key_page(
        keys=context.api_keys.list_keys(actor.id),
        revoked=request.args.get("revoked") == "1",
        requires_export_access=(
            context.installation_settings is not None
            and context.installation_settings.requires_export_access(actor.id)
        ),
    )


def _api_key_creation_form(actor: Any, session: Any, *, error=None, status=200):
    context = _context()
    identity = str(uuid4())
    signature = hmac.digest(context.review_secret, (identity + session.id).encode(), "sha256").hex()
    return _api_key_page(
        "create",
        creation_token=identity + "." + signature,
        default_expiry=(context.clock.today() + LIFETIME).isoformat(),
        error=error,
    ), status


@blueprint.route("/settings/api-keys/new", methods=["GET", "POST"])
@authenticated_route
@no_store_route
def api_key_create():
    context = _context()
    actor, session = context.require_authenticated()
    api_key_service = _require_api_keys()
    if request.method == "GET":
        return _api_key_creation_form(actor, session)
    context.require_csrf(session.csrf_token)
    try:
        if any(len(request.form.getlist(k)) != 1 for k in ("name", "expires_at", "creation_token")):
            raise ValidationError("Complete each field once")
        token = request.form["creation_token"]
        if (
            re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[0-9a-f]{64}",
                token,
            )
            is None
        ):
            raise ValidationError("Invalid creation form. Reload and try again")
        identity, signature = token.split(".", 1)
        expected = hmac.digest(
            context.review_secret, (identity + session.id).encode(), "sha256"
        ).hex()
        if not hmac.compare_digest(signature, expected):
            raise ValidationError("Invalid creation form. Reload and try again")
        value = request.form["expires_at"]
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValidationError("Choose a valid expiry date")
        try:
            expiry = datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=UTC)
        except ValueError:
            raise ValidationError("Choose a valid expiry date") from None
        # The displayed default date means exactly 90 days, not midnight.
        if value == (context.clock.today() + LIFETIME).isoformat():
            expiry = None
        created = api_key_service.create_key(
            actor.id, request.form["name"], expiry, request_id=identity
        )
    except PersistenceConflictError:
        return _api_key_creation_form(
            actor,
            session,
            error="The operation could not be completed. Reload and try again.",
            status=409,
        )
    except (ValidationError, ConflictError) as error:
        return _api_key_creation_form(actor, session, error=str(error), status=400)
    return _api_key_page("secret", created=created), 201


@blueprint.route("/settings/api-keys/<key_id>/revoke", methods=["GET", "POST"])
@authenticated_route
@no_store_route
def api_key_revoke(key_id: str):
    context = _context()
    actor, _session = context.require_authenticated()
    api_key_service = _require_api_keys()
    try:
        key = api_key_service.get_key(actor.id, key_id)
    except NotFoundError:
        return "API key not found", 404
    error = None
    if request.method == "POST":
        try:
            revision = parse_expected_revision(request.form.get("revision"))
            if len(request.form.getlist("revision")) != 1 or request.form.getlist("confirm") != [
                "yes"
            ]:
                raise ValidationError("Confirm permanent revocation")
            api_key_service.revoke_key(actor.id, key_id, revision, confirmed=True)
        except (ValidationError, ConflictError) as failure:
            error = str(failure)
        except PersistenceConflictError:
            error = "The operation could not be completed. Reload and try again."
        else:
            return redirect(url_for("settings.api_key_list", revoked="1") + "#key-status", code=303)
    return _api_key_page("revoke", key=key, error=error), 400 if error else 200


@blueprint.route("/settings/dataset-access", methods=["GET", "POST"])
@capability_route("can_administer_installation")
def dataset_access_settings():
    context = _context()
    actor, login_session = context.require_capability("can_administer_installation")
    context.require_dataset_dependencies()
    if context.installation_settings is None:
        abort(404)
    policy = context.installation_settings.get_policy(actor.id)
    expected, selected = policy, policy
    error, confirmation, status = None, None, 200
    if request.method == "POST":
        context.require_csrf(login_session.csrf_token)
        expected = request.form.get("expected_policy", "")
        selected = request.form.get("policy", "")
        try:
            context.installation_settings.change_policy(
                actor.id,
                expected,
                selected,
                confirmed=request.form.get("confirmed") == "yes",
            )
        except PolicyConfirmationRequired as exc:
            confirmation = str(exc)
        except ConflictError as exc:
            error, status = str(exc), 409
        except ValidationError as exc:
            error, status = str(exc), 400
        else:
            return redirect(url_for("settings.dataset_access_settings", notice="saved"), code=303)
    return context.render_authenticated_template(
        "pages/settings/dataset_access.html",
        active_navigation="dataset_access",
        policies=EXPORT_POLICIES,
        selected=selected,
        expected=expected,
        error=error,
        confirmation=confirmation,
        current_csrf=login_session.csrf_token,
    ), status


@blueprint.post("/settings/theme")
@authenticated_route
def theme_preference():
    context = _context()
    actor, login_session = context.require_authenticated()
    context.require_csrf(login_session.csrf_token)
    try:
        context.users.update_theme_preference(actor.id, request.form.get("theme_preference", ""))
    except ValidationError:
        abort(400)
    return redirect(url_for("settings.appearance_settings"), code=303)


@blueprint.get("/settings/appearance")
@authenticated_route
def appearance_settings():
    context = _context()
    actor, login_session = context.require_authenticated()
    return context.render_authenticated_template(
        "pages/settings/appearance.html",
        settings_title="Appearance",
        active_navigation="appearance",
        theme_preference=actor.theme_preference,
        current_csrf=login_session.csrf_token,
    )


def register_settings_blueprint(
    app: Flask,
    *,
    users: UserService,
    api_keys: ApiKeyService | None,
    installation_settings: InstallationSettingsService | None,
    clock: Clock,
    review_secret: bytes,
    require_authenticated: Callable[[], tuple[Any, Any]],
    require_capability: CapabilityResolver,
    require_csrf: Callable[[str | None], None],
    require_dataset_dependencies: Callable[[], Any],
    render_authenticated_template: Callable[..., Any],
) -> None:
    app.extensions["refrncr.settings"] = SettingsContext(
        users=users,
        api_keys=api_keys,
        installation_settings=installation_settings,
        clock=clock,
        review_secret=review_secret,
        require_authenticated=require_authenticated,
        require_capability=require_capability,
        require_csrf=require_csrf,
        require_dataset_dependencies=require_dataset_dependencies,
        render_authenticated_template=render_authenticated_template,
    )
    app.register_blueprint(blueprint)

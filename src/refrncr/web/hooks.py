"""Shared request and response hooks for browser routes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from flask import Flask, abort, g, redirect, request, url_for

from refrncr.browser_security import WEB_HTML_HEADERS
from refrncr.ports import PersistenceConflictError
from refrncr.record_import import MAX_COLUMNS, MAX_UPLOAD_BYTES
from refrncr.services import AuthorizationError, ConflictError, NotFoundError
from refrncr.web.helpers import safe_login_target


def register_request_limit_hook(app: Flask) -> None:
    """Apply route-specific upload and import-form limits."""

    @app.before_request
    def import_upload_limit():
        view = app.view_functions.get(request.endpoint)
        if getattr(view, "upload_limit_route", False) and request.method == "POST":
            request.max_content_length = MAX_UPLOAD_BYTES + 64 * 1024
        elif getattr(view, "bounded_import_route", False) and request.method == "POST":
            # Six bounded controls per column, plus CSRF and optional navigation.
            # Multipart framing must fit the existing 250-column upload contract.
            request.max_content_length = MAX_COLUMNS * 1024
            request.max_form_parts = MAX_COLUMNS * 6 + 2


def register_security_hooks(
    app: Flask,
    *,
    authenticated: Callable[[], Any],
    cookie_settings: Callable[[], Any],
    require_csrf: Callable[[str | None], None],
    require_capability: Callable[[str], Any],
    authorization: Any,
    datasets: Any,
) -> None:
    """Register authentication, CSRF and capability enforcement in order."""

    @app.before_request
    def redirect_anonymous_browser_request():
        # Only registered browser endpoints participate; API/health is owned by FastAPI.
        view = app.view_functions.get(request.endpoint)
        if (
            request.endpoint
            and request.endpoint != "static"
            and not getattr(view, "public_route", False)
            and authenticated() is None
        ):
            target = safe_login_target(request.path) if request.method in {"GET", "HEAD"} else "/"
            return redirect(url_for("login", next=target))
        return None

    @app.before_request
    def enforce_csrf_for_state_changes():
        """Protect every browser state change independently of endpoint names."""
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        view = app.view_functions.get(request.endpoint)
        if request.endpoint is None or view is None:
            return None
        settings = cookie_settings()
        if not getattr(view, "anonymous_csrf_route", False) and (
            settings is None or request.cookies.get(settings.session_name) is None
        ):
            return None
        resolved = authenticated()
        expected = (
            request.cookies.get(settings.anonymous_csrf_name)
            if getattr(view, "anonymous_csrf_route", False) and settings
            else resolved[1].csrf_token
            if resolved is not None
            else request.cookies.get(settings.anonymous_csrf_name)
            if settings
            else None
        )
        require_csrf(expected)
        return None

    @app.before_request
    def enforce_route_capability():
        view = app.view_functions.get(request.endpoint)
        capability = getattr(view, "required_capability", None)
        if capability is None:
            return None
        resolved = authenticated()
        if resolved is None:
            return None
        if capability in {
            "can_access_community",
            "can_administer_installation",
            "can_manage_access",
            "can_create_dataset",
        }:
            require_capability(capability)
            return None
        if capability not in {
            "can_view_dataset",
            "can_maintain_dataset",
            "can_export_dataset",
            "can_create_dataset",
            "can_manage_export_access",
        }:
            abort(500)
        key = (request.view_args or {}).get("key")
        dataset_id = (request.view_args or {}).get("dataset_id")
        if key is None and dataset_id is None:
            return None
        if authorization is None or datasets is None:
            abort(404)
        with datasets.uow() as uow:
            dataset = (
                uow.datasets.get(dataset_id)
                if dataset_id is not None
                else uow.datasets.find_by_key(key)
            )
        if dataset is None or not authorization.has_capability(
            resolved[0].id, capability, dataset.id
        ):
            abort(404)
        return None


def register_concept_hooks(
    app: Flask,
    *,
    dataset_ui_enabled: bool,
    require_authenticated: Callable[[], Any],
    require_csrf: Callable[[str | None], None],
    datasets: Any,
    dataset_versions: Any,
    render_authenticated_template: Callable[..., Any],
    authenticated: Callable[[], Any],
) -> None:
    """Bind draft concept context and expose it to templates."""

    @app.before_request
    def bind_concept_context():
        values = request.view_args or {}
        view = app.view_functions.get(request.endpoint)
        if (
            dataset_ui_enabled
            and values.get("key")
            and (getattr(view, "concept_route", False) or values.get("concept"))
        ):
            actor, session = require_authenticated()
            if request.method == "POST":
                require_csrf(session.csrf_token)
            try:
                datasets.get_dataset_for_edit(actor.id, values["key"])
                g.concept_ui = dataset_versions.concept_context(actor.id, values["key"])
            except NotFoundError:
                abort(404)
            except ConflictError as error:
                return render_authenticated_template(
                    "pages/datasets/concept_required.html",
                    key=values["key"],
                    error=str(error),
                    has_current=dataset_versions.history(actor.id, values["key"]).has_current,
                ), 409
        return None

    @app.context_processor
    def concept_template_context():
        return {"concept_ui": getattr(g, "concept_ui", None)}

    @app.context_processor
    def theme_template_context():
        resolved = authenticated()
        return {"current_theme_preference": resolved[0].theme_preference if resolved else "system"}


def register_response_hooks(app: Flask) -> None:
    """Register security headers, cookie cleanup and no-store policy."""

    @app.after_request
    def protect_browser_response(response):
        # Flask only: the mounted API and documentation own their response policies.
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if response.mimetype == "text/html":
            response.headers.update(WEB_HTML_HEADERS)
            response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.after_request
    def clear_stale_session_cookie(response):
        settings = getattr(g, "stale_cookie_settings", None)
        if settings is not None:
            response.delete_cookie(settings.session_name, **settings.common_options)
        return response

    @app.after_request
    def protect_sensitive_response(response):
        endpoint = request.endpoint or ""
        view = app.view_functions.get(endpoint)
        if getattr(view, "no_store_route", False):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        if getattr(view, "concept_route", False) or (request.view_args or {}).get("concept", False):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = (
                "no-referrer" if "review" in request.args else "same-origin"
            )
        return response


def register_dataset_visit_hook(
    app: Flask,
    *,
    dataset_ui_enabled: bool,
    authenticated: Callable[[], Any],
    overview_service: Any,
) -> None:
    """Record successful dataset page visits without affecting the response."""

    @app.after_request
    def record_dataset_visit(response):
        values = request.view_args or {}
        if (
            dataset_ui_enabled
            and request.method == "GET"
            and response.status_code == 200
            and response.mimetype == "text/html"
            and request.path.startswith("/datasets/")
            and (values.get("key") or values.get("dataset_id"))
        ):
            resolved = authenticated()
            if resolved:
                try:
                    overview_service.record_visit(
                        resolved[0].id,
                        key=values.get("key"),
                        dataset_id=values.get("dataset_id"),
                    )
                except (AuthorizationError, PersistenceConflictError):
                    # Concurrent deletion/revocation or a history-write conflict must not
                    # replace an already-authorized page with a telemetry failure.
                    logging.getLogger(__name__).warning("Dataset visit could not be recorded")
        return response

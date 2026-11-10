"""Route metadata used by the shared browser request hooks."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any

from flask import Blueprint, Flask, abort, current_app, g
from werkzeug.exceptions import HTTPException

from refrncr.services import AuthorizationError, NotFoundError


@dataclass(frozen=True)
class WebRoutingContext:
    dataset_ui_enabled: bool
    audit_security: Callable[..., None]
    render_authenticated_template: Callable[..., Any]


def register_web_routing_context(
    app: Flask,
    *,
    dataset_ui_enabled: bool,
    audit_security: Callable[..., None],
    render_authenticated_template: Callable[..., Any],
) -> None:
    app.extensions["refrncr.web.routing"] = WebRoutingContext(
        dataset_ui_enabled=dataset_ui_enabled,
        audit_security=audit_security,
        render_authenticated_template=render_authenticated_template,
    )


class DatasetBlueprint(Blueprint):
    """Blueprint with routes conditional on configured dataset browser services."""

    def dataset_route(self, rule: str, *, capability: str, **options: Any):
        def decorate(view):
            view.required_capability = capability
            self.record(
                lambda state: (
                    state.add_url_rule(rule, view_func=view, **options)
                    if state.app.extensions["refrncr.web.routing"].dataset_ui_enabled
                    else None
                )
            )
            return view

        return decorate


def dataset_route(
    app: Flask,
    rule: str,
    *,
    capability: str,
    **options: Any,
):
    """Register a composition route only when dataset browser services exist."""
    if app.extensions["refrncr.web.routing"].dataset_ui_enabled:

        def decorate(function):
            function.required_capability = capability
            return app.route(rule, **options)(function)

        return decorate
    return lambda function: function


def version_errors(function):
    """Apply the shared dataset-version error and audit policy."""

    @wraps(function)
    def secured(*args, **kwargs):
        context = current_app.extensions["refrncr.web.routing"]
        try:
            return function(*args, **kwargs)
        except AuthorizationError:
            resolved = getattr(g, "authenticated_resolution", None)
            context.audit_security(
                "security.authorization_denied",
                actor_user_id=resolved[0].id if resolved else None,
            )
            abort(403)
        except NotFoundError:
            abort(404)
        except HTTPException:
            raise
        except Exception as error:
            logging.getLogger(function.__module__).error(
                "Dataset version operation failed: %s", type(error).__name__, exc_info=True
            )
            return (
                context.render_authenticated_template(
                    "pages/error.html",
                    error_code=500,
                    error_message="Dataset versions could not be loaded. Please try again.",
                ),
                500,
            )

    return secured


def public_route[View: Callable[..., Any]](view: View) -> View:
    """Mark a browser route that may be reached without an active session."""
    view.public_route = True
    return view


def anonymous_csrf_route[View: Callable[..., Any]](view: View) -> View:
    """Mark a route whose state changes use the anonymous CSRF cookie."""
    view.anonymous_csrf_route = True
    return view


def concept_route[View: Callable[..., Any]](view: View) -> View:
    """Mark a route that needs concept context and no-store response headers."""
    view.concept_route = True
    return view


def capability_route[View: Callable[..., Any]](capability: str) -> Callable[[View], View]:
    """Declare the capability required by a browser route."""

    def decorate(view: View) -> View:
        view.required_capability = capability
        return view

    return decorate


def authenticated_route[View: Callable[..., Any]](view: View) -> View:
    """Declare the capability shared by every authenticated browser route."""
    return capability_route("can_access_community")(view)


def no_store_route[View: Callable[..., Any]](view: View) -> View:
    """Mark a response as containing sensitive non-cacheable content."""
    view.no_store_route = True
    return view


def upload_limit_route[View: Callable[..., Any]](view: View) -> View:
    """Apply the bounded upload request policy to a route."""
    view.upload_limit_route = True
    return view


def bounded_import_route[View: Callable[..., Any]](view: View) -> View:
    """Apply the bounded import-form request policy to a route."""
    view.bounded_import_route = True
    return view


def dataset_not_found_route[View: Callable[..., Any]](view: View) -> View:
    """Use the uniform dataset-not-found response contract for a route."""
    view.dataset_not_found_route = True
    return view

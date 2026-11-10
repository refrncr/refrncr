"""Shared HTML error policy for the Community browser application."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from flask import Flask, abort, g, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from refrncr.services import AuthorizationError, ConflictError, NotFoundError, UserManagementError


def register_error_handlers(
    app: Flask,
    render_authenticated_template: Callable[..., str],
    *,
    generic_login_failure: str,
) -> None:
    """Register the browser error policy on ``app``."""

    @app.errorhandler(RequestEntityTooLarge)
    def handle_oversized_request(_error: RequestEntityTooLarge):
        from flask import request

        if request.path == "/login":
            return generic_login_failure, 401
        return "Request is too large", 413

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        if error.code in {403, 404, 405, 500}:
            messages = {
                403: "You are not allowed to view this page.",
                404: "The requested page could not be found.",
                405: "This action is not supported for this page.",
                500: "Something went wrong. Please try again.",
            }
            return (
                render_authenticated_template(
                    "pages/error.html",
                    error_code=error.code,
                    error_message=messages[error.code],
                ),
                error.code,
            )
        return error

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Any):
        logging.getLogger(__name__).exception(
            "Unhandled web request failed: %s", type(error).__name__
        )
        return (
            render_authenticated_template(
                "pages/error.html",
                error_code=500,
                error_message="Something went wrong. Please try again.",
            ),
            500,
        )


def register_user_error_handler(app: Flask, audit_security: Callable[..., None]) -> None:
    """Register the shared service error policy after all browser blueprints."""

    @app.errorhandler(UserManagementError)
    def handle_user_error(error: UserManagementError):
        view = app.view_functions.get(request.endpoint)
        if isinstance(error, NotFoundError) and getattr(view, "dataset_not_found_route", False):
            return "Dataset not found", 404
        if isinstance(error, NotFoundError):
            abort(404)
        if isinstance(error, AuthorizationError):
            resolved = getattr(g, "authenticated_resolution", None)
            audit_security(
                "security.authorization_denied",
                actor_user_id=resolved[0].id if resolved else None,
            )
        if isinstance(error, ConflictError):
            return str(error), 409
        status = 403 if isinstance(error, AuthorizationError) else 400
        return str(error), status

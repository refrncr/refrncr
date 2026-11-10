"""Shared HTML error policy for the Community browser application."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from flask import Flask
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge


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

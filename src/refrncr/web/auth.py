"""Authentication and initial-setup browser blueprint."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from flask import Blueprint, Flask, redirect, request, url_for

from refrncr.browser_security import BrowserCookieSettings
from refrncr.hierarchy_import import HierarchyImportService
from refrncr.login_security import (
    MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS,
    BrowserAuthenticationAdmissionGuard,
    LoginAttemptLimiter,
    SourceIdentityResolver,
    canonical_account_key,
)
from refrncr.record_import import DatasetRecordImportService
from refrncr.services import AuthenticationService, SessionService, UserManagementError, UserService
from refrncr.web.helpers import is_safe_login_target
from refrncr.web.routing import anonymous_csrf_route, public_route

blueprint = Blueprint("auth", __name__)


class CookieSettingsResolver(Protocol):
    def __call__(self) -> BrowserCookieSettings | None: ...


class RequiredCookieSettingsResolver(Protocol):
    def __call__(self) -> BrowserCookieSettings: ...


class CsrfValidator(Protocol):
    def __call__(self, expected_token: str | None) -> None: ...


class AnonymousTemplateRenderer(Protocol):
    def __call__(self, template_name: str, **context: Any) -> Any: ...


class AnonymousCsrfFormRenderer(Protocol):
    def __call__(self, template_name: str, **context: Any) -> Any: ...


class SecurityAuditor(Protocol):
    def __call__(self, action: str) -> None: ...


@dataclass(frozen=True)
class AuthContext:
    users: UserService
    authentication: AuthenticationService
    sessions: SessionService
    record_imports: DatasetRecordImportService | None
    hierarchy_imports: HierarchyImportService | None
    login_limiter: LoginAttemptLimiter
    source_resolver: SourceIdentityResolver
    authentication_admission: BrowserAuthenticationAdmissionGuard
    cookie_settings: CookieSettingsResolver
    require_cookie_settings: RequiredCookieSettingsResolver
    require_csrf: CsrfValidator
    render_anonymous_template: AnonymousTemplateRenderer
    anonymous_csrf_form_response: AnonymousCsrfFormRenderer
    audit_security: SecurityAuditor
    generic_login_failure: str


def _context() -> AuthContext:
    from flask import current_app

    return current_app.extensions["refrncr.auth"]


@blueprint.route("/setup", methods=["GET", "POST"])
@public_route
@anonymous_csrf_route
def setup():
    context = _context()
    settings = context.require_cookie_settings()
    if context.users.has_users():
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        context.require_csrf(request.cookies.get(settings.anonymous_csrf_name))
        try:
            context.users.create_initial_admin(
                request.form["email"],
                request.form["display_name"],
                request.form["password"],
            )
        except UserManagementError as error:
            message = str(error)
            error_field = getattr(error, "field", None)
            return (
                context.render_anonymous_template(
                    "pages/auth/setup.html",
                    csrf_token=request.cookies.get(settings.anonymous_csrf_name),
                    error=message,
                    error_field=error_field,
                    email=request.form["email"],
                    display_name=request.form["display_name"],
                ),
                400,
            )
        response = redirect(url_for("auth.login"))
        response.delete_cookie(settings.anonymous_csrf_name, **settings.common_options)
        return response
    return context.anonymous_csrf_form_response(
        "pages/auth/setup.html",
        error_field=None,
        email=None,
        display_name=None,
    )


@blueprint.route("/login", methods=["GET", "POST"])
@public_route
@anonymous_csrf_route
def login():
    context = _context()
    settings = context.require_cookie_settings()
    submitted_target = (
        request.form.get("next") if request.method == "POST" else request.args.get("next")
    )
    # Rebuild the target from a constant root: a checked path stays the same, and the
    # redirect can never leave this origin (CodeQL py/url-redirection).
    return_target = "/"
    if submitted_target and is_safe_login_target(submitted_target):
        return_target = "/" + submitted_target.removeprefix("/")

    def invalid_credentials_response():
        return (
            context.render_anonymous_template(
                "pages/auth/login.html",
                csrf_token=request.cookies.get(settings.anonymous_csrf_name),
                error=context.generic_login_failure,
                next_target=return_target,
                field_errors={},
                email=request.form.get("email", "")[:MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS],
            ),
            401,
        )

    if request.method == "POST":
        context.require_csrf(request.cookies.get(settings.anonymous_csrf_name))
        submitted_email = request.form.get("email", "")
        submitted_password = request.form.get("password", "")
        field_errors = {}
        if not submitted_email.strip():
            field_errors["email"] = "Enter your email address."
        if not submitted_password:
            field_errors["password"] = "Enter your password."
        oversized_identifier = len(submitted_email) > MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS
        account_key = canonical_account_key(submitted_email)
        source_key = context.source_resolver.resolve(
            request.remote_addr,
            request.headers.get("X-Forwarded-For"),
        )
        attempt = context.login_limiter.reserve(source_key, account_key)
        if attempt is None:
            context.audit_security("security.login_failed")
            return invalid_credentials_response()
        if oversized_identifier:
            attempt.failed()
            context.audit_security("security.login_failed")
            return invalid_credentials_response()
        if field_errors:
            attempt.failed()
            return context.render_anonymous_template(
                "pages/auth/login.html",
                csrf_token=request.cookies.get(settings.anonymous_csrf_name),
                error="Check the required fields below.",
                field_errors=field_errors,
                email=submitted_email[:MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS],
                next_target=return_target,
            ), 400
        permit = context.authentication_admission.try_acquire()
        if permit is None:
            attempt.cancel()
            context.audit_security("security.login_failed")
            return invalid_credentials_response()
        try:
            with permit:
                principal = context.authentication.authenticate(submitted_email, submitted_password)
        except Exception:
            logging.getLogger(__name__).exception("Browser authentication failed")
            attempt.failed()
            raise
        if principal is not None:
            attempt.succeeded()
            existing_token = request.cookies.get(settings.session_name)
            if existing_token:
                context.sessions.revoke(existing_token)
            token, _ = context.sessions.create(principal)
            response = redirect(return_target)
            response.set_cookie(
                settings.session_name,
                token,
                max_age=settings.session_max_age,
                **settings.common_options,
            )
            response.delete_cookie(settings.anonymous_csrf_name, **settings.common_options)
            return response
        attempt.failed()
        context.audit_security("security.login_failed")
        return invalid_credentials_response()
    return context.anonymous_csrf_form_response(
        "pages/auth/login.html", next_target=return_target, field_errors={}, email=None
    )


@blueprint.post("/logout")
@public_route
def logout():
    context = _context()
    settings = context.cookie_settings()
    token = request.cookies.get(settings.session_name) if settings else None
    if not token:
        return redirect(url_for("overview.index"))
    resolved = context.sessions.resolve_with_session(token)
    if resolved is None:
        return redirect(url_for("overview.index"))
    context.require_csrf(resolved[1].csrf_token)
    if context.record_imports is not None:
        context.record_imports.revoke_session(resolved[1].id)
    if context.hierarchy_imports is not None:
        context.hierarchy_imports.revoke_session(resolved[1].id)
    context.sessions.revoke(token)
    response = redirect(url_for("overview.index"))
    response.delete_cookie(settings.session_name, **settings.common_options)
    return response


def register_auth_blueprint(
    app: Flask,
    *,
    users: UserService,
    authentication: AuthenticationService,
    sessions: SessionService,
    record_imports: DatasetRecordImportService | None,
    hierarchy_imports: HierarchyImportService | None,
    login_limiter: LoginAttemptLimiter,
    source_resolver: SourceIdentityResolver,
    authentication_admission: BrowserAuthenticationAdmissionGuard,
    cookie_settings: CookieSettingsResolver,
    require_cookie_settings: RequiredCookieSettingsResolver,
    require_csrf: CsrfValidator,
    render_anonymous_template: AnonymousTemplateRenderer,
    anonymous_csrf_form_response: AnonymousCsrfFormRenderer,
    audit_security: SecurityAuditor,
    generic_login_failure: str,
) -> None:
    app.extensions["refrncr.auth"] = AuthContext(
        users=users,
        authentication=authentication,
        sessions=sessions,
        record_imports=record_imports,
        hierarchy_imports=hierarchy_imports,
        login_limiter=login_limiter,
        source_resolver=source_resolver,
        authentication_admission=authentication_admission,
        cookie_settings=cookie_settings,
        require_cookie_settings=require_cookie_settings,
        require_csrf=require_csrf,
        render_anonymous_template=render_anonymous_template,
        anonymous_csrf_form_response=anonymous_csrf_form_response,
        audit_security=audit_security,
        generic_login_failure=generic_login_failure,
    )
    app.register_blueprint(blueprint)

from __future__ import annotations

import hmac
import logging
import re
import secrets
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import wraps
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from flask import (
    Flask,
    abort,
    g,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from refrncr.api_keys import LIFETIME, ApiKeyService
from refrncr.audit import record_security_event
from refrncr.audit_history import AuditHistoryService, filter_values, parse_audit_filters
from refrncr.audit_presentation import ACTION_LABELS, OBJECTS
from refrncr.browser_security import (
    HTTPS_ANONYMOUS_CSRF_COOKIE,
    HTTPS_SESSION_COOKIE,
    BrowserCookiePolicy,
)
from refrncr.change_status import (
    DISPLAY_STATUSES,
    RECORD_STATUS_FILTERS,
    ChangeStatus,
    change_status,
    status_badge_variant,
    status_label,
)
from refrncr.clock import SYSTEM_CLOCK, Clock
from refrncr.dataset_diff import STALE_MESSAGE, DatasetDiffService
from refrncr.dataset_export import ExportGenerationError, logged_export_stream
from refrncr.dataset_versions import DatasetVersionService
from refrncr.datatypes import DATATYPES, get_datatype
from refrncr.diff_contracts import STATUSES, DiffCriteria
from refrncr.hierarchy import HierarchyService
from refrncr.hierarchy_import import HierarchyImportService
from refrncr.login_security import (
    MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS,
    BrowserAuthenticationAdmissionGuard,
    LoginAttemptLimiter,
    SourceIdentityResolver,
    canonical_account_key,
)
from refrncr.ports import AuditReadUnavailable, PersistenceConflictError
from refrncr.presentation_metadata import (
    DATASET_ICONS,
    dataset_icon_sprite,
    default_dataset_icon,
    key_from_name,
    rendered_dataset_icon,
)
from refrncr.record_import import (
    MAX_COLUMNS,
    MAX_UPLOAD_BYTES,
    PROFILE_SCAN_ROWS,
    DatasetRecordImportService,
    ImportExpiredError,
    ImportValidationError,
    SourceFormatComponentsError,
    distinct_samples,
    mapping_issue_message,
    parse_source,
)
from refrncr.record_search import (
    MAX_QUERY_BYTES,
    RecordSearchError,
    matches,
    parse_parameters,
    search_term,
)
from refrncr.review_tokens import decode_discard, decode_review, encode_discard, encode_review
from refrncr.services import (
    AuthenticationService,
    AuthorizationError,
    AuthorizationService,
    ConflictError,
    DatasetBusinessKeyService,
    DatasetFieldService,
    DatasetRecordService,
    DatasetService,
    MembershipService,
    NotFoundError,
    RecordConflictError,
    RecordValidationError,
    SessionService,
    StaleFieldOrderError,
    StaleRecordError,
    TeamService,
    UserManagementError,
    UserService,
    ValidationError,
    parse_expected_revision,
)
from refrncr.version_contracts import mapping_incomplete_message


def merge_error_messages(*messages):
    return " ".join(dict.fromkeys(message for message in messages if message))


ANONYMOUS_CSRF_COOKIE = HTTPS_ANONYMOUS_CSRF_COOKIE
SESSION_COOKIE = HTTPS_SESSION_COOKIE
GENERIC_LOGIN_FAILURE = "Invalid credentials"
MAX_BROWSER_FORM_CONTENT_LENGTH = 16 * 1024


def public_route(view):
    """Mark a browser route that may be reached without an active session."""
    view.public_route = True
    return view


def anonymous_csrf_route(view):
    """Mark a route whose state changes use the anonymous CSRF cookie."""
    view.anonymous_csrf_route = True
    return view


def concept_route(view):
    """Mark a route that needs concept context and no-store response headers."""
    view.concept_route = True
    return view


def capability_route(capability):
    """Declare the capability required by a browser route."""

    def decorate(view):
        view.required_capability = capability
        return view

    return decorate


def authenticated_route(view):
    """Declare the capability shared by every authenticated browser route."""
    return capability_route("can_access_community")(view)


def no_store_route(view):
    view.no_store_route = True
    return view


def upload_limit_route(view):
    view.upload_limit_route = True
    return view


def bounded_import_route(view):
    view.bounded_import_route = True
    return view


def dataset_not_found_route(view):
    view.dataset_not_found_route = True
    return view


def is_safe_login_target(target: str | None) -> bool:
    """Return whether a login target is a safe root-relative path."""
    if not target:
        return True
    decoded = unquote(target)
    if (
        not decoded.startswith("/")
        or decoded.startswith("//")
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in decoded
        )
        or any(character in decoded for character in "\\%?#")
        or any(segment in {".", ".."} for segment in decoded.split("/"))
    ):
        return False
    parsed = urlsplit(decoded)
    if parsed.scheme or parsed.netloc or parsed.path in {"/login", "/logout", "/setup"}:
        return False
    return True


def safe_login_target(target: str | None) -> str:
    """Accept only root-relative paths, never URLs or query/fragment payloads."""
    return target if is_safe_login_target(target) else "/"


def create_flask_app(
    users: UserService,
    authentication: AuthenticationService,
    sessions: SessionService,
    teams: TeamService,
    memberships: MembershipService,
    *,
    authorization: AuthorizationService | None = None,
    datasets: DatasetService | None = None,
    dataset_fields: DatasetFieldService | None = None,
    dataset_business_keys: DatasetBusinessKeyService | None = None,
    dataset_records: DatasetRecordService | None = None,
    record_imports: DatasetRecordImportService | None = None,
    hierarchy_imports: HierarchyImportService | None = None,
    allow_loopback_http: bool = False,
    login_limiter: LoginAttemptLimiter | None = None,
    source_resolver: SourceIdentityResolver | None = None,
    authentication_admission: BrowserAuthenticationAdmissionGuard | None = None,
    audit_history: AuditHistoryService | None = None,
    dataset_versions: DatasetVersionService | None = None,
    review_signing_key: bytes | None = None,
    api_keys: ApiKeyService | None = None,
    clock: Clock | None = None,
) -> Flask:
    app = Flask(__name__)
    clock = clock or getattr(
        api_keys, "clock", getattr(getattr(users, "uow", None), "clock", SYSTEM_CLOCK)
    )
    security_uow = getattr(users, "uow", None)

    def audit_security(action, *, actor_user_id=None):
        # Small interface/unit doubles used by tests may not compose persistence.
        # Production Community UserService always supplies the shared UoW factory.
        if security_uow is not None:
            record_security_event(security_uow, action, actor_user_id=actor_user_id)

    review_secret = (
        review_signing_key if review_signing_key is not None else secrets.token_bytes(32)
    )
    audit_history = audit_history or AuditHistoryService(users.uow, clock=clock)

    app.jinja_env.globals["rendered_dataset_icon"] = rendered_dataset_icon
    app.jinja_env.globals["dataset_icon_sprite"] = dataset_icon_sprite
    app.jinja_env.globals["default_dataset_icon"] = default_dataset_icon
    app.jinja_env.filters["datatype_label"] = lambda value: get_datatype(value).label
    app.jinja_env.filters["dataset_title"] = lambda value: value.replace("->", "→")
    app.jinja_env.filters["status_label"] = status_label
    app.jinja_env.filters["status_badge_variant"] = status_badge_variant
    app.jinja_env.globals["ChangeStatus"] = ChangeStatus
    app.jinja_env.globals["DISPLAY_STATUSES"] = DISPLAY_STATUSES
    app.jinja_env.globals["status_label"] = status_label

    @app.template_filter("record_value_text")
    def record_value_text(value, data_type):
        return "" if value is None else get_datatype(data_type).text(value)

    @app.template_filter("absolute_utc")
    def absolute_utc(value):
        if value.utcoffset() is None:
            raise ValueError("Database timestamps must be timezone-aware")
        value = value.astimezone(UTC)
        months = (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        )
        return f"{value.day} {months[value.month - 1]} {value.year}, {value:%H:%M} UTC"

    @app.template_filter("short_date")
    def short_date(value):
        months = (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        )
        return f"{value.day} {months[value.month - 1]} {value.year}"

    app.config["MAX_CONTENT_LENGTH"] = MAX_BROWSER_FORM_CONTENT_LENGTH

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

    login_limiter = login_limiter or LoginAttemptLimiter(clock=clock)
    source_resolver = source_resolver or SourceIdentityResolver()
    authentication_admission = authentication_admission or BrowserAuthenticationAdmissionGuard()
    existing_dataset_dependencies = (
        authorization,
        datasets,
        dataset_fields,
        dataset_business_keys,
    )
    if any(dependency is not None for dependency in existing_dataset_dependencies) and not all(
        dependency is not None for dependency in existing_dataset_dependencies
    ):
        raise ValueError("Dataset browser dependencies must be supplied together")
    if all(dependency is not None for dependency in existing_dataset_dependencies):
        dataset_records = dataset_records or DatasetRecordService(datasets.uow, authorization)
        record_imports = record_imports or DatasetRecordImportService(dataset_records)
        dataset_versions = (
            dataset_versions
            or datasets.versions
            or DatasetVersionService(datasets.uow, authorization.datasets)
        )
        hierarchy = HierarchyService(datasets.uow, authorization, versions=dataset_versions)
    elif dataset_records is not None:
        raise ValueError("Dataset browser dependencies must be supplied together")
    dataset_ui_enabled = dataset_records is not None
    dataset_diff = DatasetDiffService(dataset_versions) if dataset_ui_enabled else None
    cookie_policy = BrowserCookiePolicy(
        session_max_age=int(sessions.lifetime.total_seconds()),
        allow_loopback_http=allow_loopback_http,
    )

    def cookie_settings():
        return cookie_policy.for_request(
            scheme=request.scheme,
            host=request.host,
            remote_address=request.remote_addr,
        )

    def require_cookie_settings():
        settings = cookie_settings()
        if settings is None:
            abort(403, description="Authenticated browser access requires HTTPS")
        return settings

    @app.after_request
    def clear_stale_session_cookie(response):
        settings = getattr(g, "stale_cookie_settings", None)
        if settings is not None:
            response.delete_cookie(settings.session_name, **settings.common_options)
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def handle_oversized_request(_error):
        if request.path == "/login":
            return GENERIC_LOGIN_FAILURE, 401
        return "Request is too large", 413

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
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
    def handle_unexpected_error(error):
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

    authentication_cache_missing = object()

    def authenticated():
        cached = getattr(g, "authenticated_resolution", authentication_cache_missing)
        if cached is not authentication_cache_missing:
            return cached
        settings = cookie_settings()
        if settings is None:
            g.authenticated_resolution = None
            return None
        token = request.cookies.get(settings.session_name)
        if not token:
            g.authenticated_resolution = None
            return None
        resolved = sessions.resolve_with_session(token)
        if resolved is None:
            audit_security("security.session_rejected")
            g.stale_cookie_settings = settings
        g.authenticated_resolution = resolved
        return resolved

    @app.before_request
    def redirect_anonymous_browser_request():
        # Only registered browser endpoints participate; API/health is owned by FastAPI.
        view = app.view_functions.get(request.endpoint)
        if (
            request.endpoint
            and request.endpoint != "static"
            and not getattr(view, "public_route", False)
        ):
            if authenticated() is None:
                target = (
                    safe_login_target(request.path) if request.method in {"GET", "HEAD"} else "/"
                )
                return redirect(url_for("login", next=target))

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

    def active_navigation() -> str:
        endpoint = request.endpoint or ""
        if endpoint.startswith("api_key_"):
            return "api_keys"
        if endpoint.startswith("audit_"):
            return "audit"
        if endpoint == "index":
            return "home"
        if endpoint.startswith("dataset_") or endpoint in {
            "new_dataset",
            "create_dataset",
            "edit_dataset",
            "update_dataset",
            "new_dataset_field",
            "create_dataset_field",
            "edit_dataset_field",
            "update_dataset_field",
            "delete_dataset_field",
            "import_dataset_records",
            "select_import_worksheet",
            "review_import_mapping",
            "review_source_formats",
            "preview_record_import",
            "confirm_record_import",
            "cancel_record_import",
            "export_dataset_records",
        }:
            return "datasets"
        if endpoint.startswith("user_"):
            return "users"
        if endpoint.startswith("team_"):
            return "teams"
        return ""

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
                    "pages/datasets/concept_required.html", key=values["key"], error=str(error)
                ), 409

    @app.context_processor
    def concept_template_context():
        return {"concept_ui": getattr(g, "concept_ui", None)}

    @app.context_processor
    def theme_template_context():
        resolved = authenticated()
        return {"current_theme_preference": resolved[0].theme_preference if resolved else "system"}

    def render_authenticated_template(template_name: str, **context):
        resolved = authenticated()
        actor = resolved[0] if resolved else None
        csrf = resolved[1].csrf_token if resolved else None
        context.setdefault("current_actor", actor)
        context.setdefault("current_csrf", csrf)
        context.setdefault("active_navigation", active_navigation())
        context.setdefault("dataset_ui_enabled", dataset_ui_enabled)
        return render_template(template_name, **context)

    def require_capability(capability: str):
        resolved = authenticated()
        allowed = resolved is not None and (
            authorization.has_capability(resolved[0].id, capability)
            if authorization is not None
            else capability == "can_access_community"
            or capability in {"can_administer_installation", "can_manage_access"}
            and resolved[0].role == "admin"
        )
        if not allowed:
            audit_security(
                "security.authorization_denied",
                actor_user_id=resolved[0].id if resolved else None,
            )
            abort(403)
        return resolved

    def require_authenticated():
        resolved = authenticated()
        if resolved is None:
            abort(403)
        return resolved

    @app.after_request
    def protect_audit_response(response):
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

    def audit_error(message, status=400):
        return render_authenticated_template("pages/audit/error.html", error=message), status

    def api_key_page(template="list", **context):
        return render_authenticated_template("pages/api_keys/" + template + ".html", **context)

    def require_api_keys():
        if api_keys is None:
            abort(503, description="API keys require server configuration")
        return api_keys

    @app.get("/settings/api-keys")
    @authenticated_route
    @no_store_route
    def api_key_list():
        actor, _session = require_authenticated()
        if api_keys is None:
            return api_key_page(keys=(), unavailable=True), 503
        return api_key_page(
            keys=api_keys.list_keys(actor.id), revoked=request.args.get("revoked") == "1"
        )

    def api_key_creation_form(actor, session, *, error=None, status=200):
        identity = str(uuid4())
        signature = hmac.digest(review_secret, (identity + session.id).encode(), "sha256").hex()
        return api_key_page(
            "create",
            creation_token=identity + "." + signature,
            default_expiry=(clock.today() + LIFETIME).isoformat(),
            error=error,
        ), status

    @app.route("/settings/api-keys/new", methods=["GET", "POST"])
    @authenticated_route
    @no_store_route
    def api_key_create():
        actor, session = require_authenticated()
        api_key_service = require_api_keys()
        if request.method == "GET":
            return api_key_creation_form(actor, session)
        require_csrf(session.csrf_token)
        try:
            if any(
                len(request.form.getlist(k)) != 1 for k in ("name", "expires_at", "creation_token")
            ):
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
            expected = hmac.digest(review_secret, (identity + session.id).encode(), "sha256").hex()
            if not hmac.compare_digest(signature, expected):
                raise ValidationError("Invalid creation form. Reload and try again")
            value = request.form["expires_at"]
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValidationError("Choose a valid expiry date")
            try:
                expiry = datetime.combine(
                    date.fromisoformat(value), datetime.min.time(), tzinfo=UTC
                )
            except ValueError:
                raise ValidationError("Choose a valid expiry date") from None
            # The displayed default date means exactly 90 days, not midnight.
            if value == (clock.today() + LIFETIME).isoformat():
                expiry = None
            created = api_key_service.create_key(
                actor.id, request.form["name"], expiry, request_id=identity
            )
        except PersistenceConflictError:
            return api_key_creation_form(
                actor,
                session,
                error="The operation could not be completed. Reload and try again.",
                status=409,
            )
        except (ValidationError, ConflictError) as error:
            return api_key_creation_form(actor, session, error=str(error), status=400)
        return api_key_page("secret", created=created), 201

    @app.route("/settings/api-keys/<key_id>/revoke", methods=["GET", "POST"])
    @authenticated_route
    @no_store_route
    def api_key_revoke(key_id):
        actor, session = require_authenticated()
        api_key_service = require_api_keys()
        try:
            key = api_key_service.get_key(actor.id, key_id)
        except NotFoundError:
            return "API key not found", 404
        error = None
        if request.method == "POST":
            try:
                revision = parse_expected_revision(request.form.get("revision"))
                if len(request.form.getlist("revision")) != 1 or request.form.getlist(
                    "confirm"
                ) != ["yes"]:
                    raise ValidationError("Confirm permanent revocation")
                api_key_service.revoke_key(actor.id, key_id, revision, confirmed=True)
            except (ValidationError, ConflictError) as failure:
                error = str(failure)
            except PersistenceConflictError:
                error = "The operation could not be completed. Reload and try again."
            else:
                return redirect(url_for("api_key_list", revoked="1") + "#key-status", code=303)
        return api_key_page("revoke", key=key, error=error), 400 if error else 200

    @app.get("/settings/audit-log")
    @capability_route("can_administer_installation")
    @no_store_route
    def audit_list():
        actor, _session = require_capability("can_administer_installation")
        try:
            if len(request.query_string) > 4096:
                raise ValidationError(
                    "The audit filter URL is too long. Clear filters to start again."
                )
            filters, cursor = parse_audit_filters(request.args.items(multi=True))
            page = audit_history.list_history(actor.id, filters, cursor)
        except AuthorizationError:
            abort(403)
        except ValidationError as error:
            return audit_error(str(error))
        except AuditReadUnavailable:
            return audit_error("Audit history is temporarily unavailable. Try again later.", 503)
        values = filter_values(filters)
        return render_authenticated_template(
            "pages/audit/list.html",
            page=page,
            filters=values,
            next_url=url_for("audit_list", **values, cursor=page.next_cursor)
            if page.next_cursor
            else None,
            previous_url=url_for("audit_list", **values, cursor=page.previous_cursor)
            if page.previous_cursor
            else None,
            actions=sorted(
                (f"{kind}:{action}", label) for (kind, action), label in ACTION_LABELS.items()
            ),
            object_types=sorted((key, value.label) for key, value in OBJECTS.items()),
        )

    @app.get("/settings/audit-log/<event_id>")
    @capability_route("can_administer_installation")
    @no_store_route
    def audit_detail(event_id):
        actor, _session = require_capability("can_administer_installation")
        try:
            item = audit_history.get_event(actor.id, event_id)
            if request.args:
                raise ValidationError("This audit detail link contains unsupported parameters.")
        except AuthorizationError:
            abort(403)
        except NotFoundError:
            abort(404)
        except ValidationError as error:
            return audit_error(str(error))
        except AuditReadUnavailable:
            return audit_error("Audit history is temporarily unavailable. Try again later.", 503)
        return render_authenticated_template("pages/audit/detail.html", item=item)

    def require_dataset_dependencies():
        if (
            authorization is None
            or datasets is None
            or dataset_fields is None
            or dataset_business_keys is None
            or dataset_records is None
        ):
            abort(404)
        return authorization, datasets, dataset_fields, dataset_business_keys, dataset_records

    def dataset_route(rule: str, *, capability: str, **options):
        if dataset_ui_enabled:

            def decorate(function):
                function.required_capability = capability
                return app.route(rule, **options)(function)

            return decorate
        return lambda function: function

    def version_errors(function):
        @wraps(function)
        def secured(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except AuthorizationError:
                resolved = getattr(g, "authenticated_resolution", None)
                audit_security(
                    "security.authorization_denied",
                    actor_user_id=resolved[0].id if resolved else None,
                )
                abort(403)
            except NotFoundError:
                abort(404)
            except HTTPException:
                raise
            except Exception as error:
                logging.getLogger(__name__).error(
                    "Dataset version operation failed: %s", type(error).__name__, exc_info=True
                )
                return (
                    render_authenticated_template(
                        "pages/error.html",
                        error_code=500,
                        error_message="Dataset versions could not be loaded. Please try again.",
                    ),
                    500,
                )

        return secured

    def require_csrf(expected_token: str | None) -> None:
        submitted_token = request.form.get("csrf_token")
        try:
            matches = (
                expected_token is not None
                and submitted_token is not None
                and hmac.compare_digest(expected_token, submitted_token)
            )
        except TypeError:
            matches = False
        if not matches:
            resolved = getattr(g, "authenticated_resolution", None)
            if resolved is not None:
                audit_security("security.csrf_rejected", actor_user_id=resolved[0].id)
            abort(403)

    def render_anonymous_template(template_name: str, **context):
        context.setdefault("current_actor", None)
        context.setdefault("current_csrf", None)
        context.setdefault("active_navigation", "")
        context.setdefault("dataset_ui_enabled", dataset_ui_enabled)
        return render_template(template_name, **context)

    def anonymous_csrf_form_response(template_name: str, **context):
        settings = require_cookie_settings()
        token = secrets.token_urlsafe(32)
        context.setdefault("error", None)
        response = make_response(
            render_anonymous_template(
                template_name,
                csrf_token=token,
                **context,
            )
        )
        response.set_cookie(
            settings.anonymous_csrf_name,
            token,
            **settings.common_options,
        )
        return response

    @app.get("/")
    @authenticated_route
    def index():
        resolved = authenticated()
        actor = resolved[0] if resolved else None
        csrf = resolved[1].csrf_token if resolved else None
        if resolved and dataset_ui_enabled:
            authorization, dataset_service, *_dependencies = require_dataset_dependencies()
            return render_template(
                "pages/datasets/list.html",
                actor=actor,
                may_create=authorization.datasets.can_create_dataset(actor.id),
                datasets=dataset_service.list_dataset_overview(actor.id),
                deletion_notice=False,
                page_title="Overview",
                empty_dataset_label="datasets",
                dataset_kind=None,
                current_actor=actor,
                current_csrf=csrf,
                active_navigation="home",
                dataset_ui_enabled=dataset_ui_enabled,
            )
        return render_template(
            "pages/index.html",
            resolved=resolved,
            current_actor=actor,
            current_csrf=csrf,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    @dataset_route("/datasets", capability="can_access_community", methods=["GET"])
    def dataset_list():
        actor, _login_session = require_authenticated()
        _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
            require_dataset_dependencies()
        )
        dataset_kind = request.args.get("kind")
        if dataset_kind not in {None, "reference", "mapping", "hierarchy"}:
            abort(404)
        visible_datasets = dataset_service.list_dataset_overview(actor.id)
        if dataset_kind is not None:
            visible_datasets = tuple(
                context for context in visible_datasets if context.dataset.kind == dataset_kind
            )
        page_title = {
            None: "Datasets",
            "reference": "Reference datasets",
            "mapping": "Mapping datasets",
            "hierarchy": "Hierarchy datasets",
        }[dataset_kind]
        return render_template(
            "pages/datasets/list.html",
            actor=actor,
            may_create=_authorization.datasets.can_create_dataset(actor.id),
            datasets=visible_datasets,
            deletion_notice=request.args.getlist("deleted") == ["1"],
            page_title=page_title,
            empty_dataset_label=page_title.lower(),
            dataset_kind=dataset_kind,
            current_actor=actor,
            current_csrf=_login_session.csrf_token,
            active_navigation=(
                f"{dataset_kind}_datasets" if dataset_kind is not None else active_navigation()
            ),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    def render_dataset_create_form(
        *,
        key: str = "",
        name: str = "",
        description: str = "",
        initial_kind: str | None = None,
        error=None,
        error_field=None,
    ):
        actor, login_session = require_authenticated()
        _auth, service, field_service, *_rest = require_dataset_dependencies()
        eligible_teams = _auth.datasets.creation_teams(actor.id)
        available_reference_tables = tuple(
            context.dataset
            for context in service.list_dataset_overview(actor.id)
            if context.dataset.kind == "reference"
        )
        bulk_field_lookup = field_service.list_fields_for_datasets
        fields_by_dataset = bulk_field_lookup(
            actor.id, tuple(table.id for table in available_reference_tables)
        )
        reference_table_fields = {
            table.id: fields_by_dataset.get(table.id, ()) for table in available_reference_tables
        }
        return render_template(
            "pages/datasets/create.html",
            dataset_icon_choices=DATASET_ICONS,
            selected_icon=request.form.get("icon", ""),
            teams=eligible_teams,
            owner_team_id=request.form.get(
                "owner_team_id", eligible_teams[0].id if len(eligible_teams) == 1 else ""
            ),
            key=key or (key_from_name(name) if not error else ""),
            name=name,
            description=description,
            table_kind=request.form.get("kind") or initial_kind or "reference",
            mapping_mode=request.form.get("mapping_mode", "standalone"),
            source_system=request.form.get("source_system", ""),
            target_system=request.form.get("target_system", ""),
            source_dataset_id=request.form.get("source_dataset_id", ""),
            target_dataset_id=request.form.get("target_dataset_id", ""),
            source_field_key=request.form.get("source_field_key", ""),
            target_field_key=request.form.get("target_field_key", ""),
            available_reference_tables=available_reference_tables,
            reference_table_fields=reference_table_fields,
            error=error,
            error_field=error_field,
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    @dataset_route("/datasets/new", capability="can_create_dataset", methods=["GET"])
    def new_dataset():
        require_dataset_dependencies()
        initial_kind = request.args.get("kind")
        if initial_kind not in {None, "reference", "mapping", "hierarchy"}:
            abort(404)
        return render_dataset_create_form(initial_kind=initial_kind)

    @dataset_route("/datasets", capability="can_create_dataset", methods=["POST"])
    def create_dataset():
        actor, login_session = require_authenticated()
        _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        key = request.form.get("key", "")
        name = request.form.get("name", "")
        description = request.form.get("description", "")
        try:
            submitted_teams = request.form.getlist("owner_team_id")
            if len(submitted_teams) != 1 or not submitted_teams[0]:
                raise ValidationError("Choose an owning team.")
            dataset = dataset_service.create_dataset(
                actor.id,
                key,
                name,
                description,
                submitted_teams[0],
                icon=request.form.get("icon", ""),
                kind=request.form.get("kind", "reference"),
                mapping_mode=request.form.get("mapping_mode") or None,
                source_system=request.form.get("source_system") or None,
                target_system=request.form.get("target_system") or None,
                source_dataset_id=request.form.get("source_dataset_id") or None,
                target_dataset_id=request.form.get("target_dataset_id") or None,
                source_field_key=request.form.get("source_field_key") or None,
                target_field_key=request.form.get("target_field_key") or None,
            )
        except (ValidationError, ConflictError) as error:
            message = str(error)
            if message.startswith("Dataset key"):
                error_field = "key"
            elif message.startswith("Dataset name"):
                error_field = "name"
            elif message.startswith("Dataset description"):
                error_field = "description"
            else:
                error_field = None
            response = render_dataset_create_form(
                key=key,
                name=name,
                description=description,
                error=message,
                error_field=error_field,
            )
            return response, 409 if isinstance(error, ConflictError) else 400
        if dataset.kind == "mapping":
            return redirect(url_for("dataset_configuration", key=dataset.key) + "#mapping")
        if dataset.kind == "hierarchy":
            return redirect(url_for("dataset_records_view", key=dataset.key, concept=True))
        return redirect(url_for("dataset_detail", key=dataset.key, concept=True))

    @dataset_route(
        "/datasets/<key>/concept",
        capability="can_view_dataset",
        methods=["GET"],
        defaults={"concept": True},
    )
    @dataset_route(
        "/datasets/<key>",
        capability="can_view_dataset",
        methods=["GET"],
        defaults={"concept": False},
    )
    @dataset_route(
        "/datasets/<key>/versions/<version_id>",
        capability="can_view_dataset",
        methods=["GET"],
        defaults={"concept": False},
    )
    @version_errors
    def dataset_detail(key: str, version_id=None, concept=False):
        return render_dataset_configuration(
            key, version_id=version_id, concept=concept, edit_mode=request.args.get("edit") == "1"
        )

    @dataset_route("/datasets/<key>/configure", capability="can_view_dataset", methods=["GET"])
    @version_errors
    def dataset_configuration(key):
        return render_dataset_configuration(
            key, standalone=True, edit_mode=request.args.get("edit") == "1"
        )

    def render_dataset_configuration(
        key,
        *,
        version_id=None,
        concept=False,
        standalone=False,
        edit_mode=False,
        name=None,
        description=None,
        error=None,
        error_field=None,
        access_error=None,
        access_subject="",
    ):
        actor, login_session = require_authenticated()
        require_dataset_dependencies()
        field_context_label = None
        overview = None
        draft_in_progress = False
        may_export = False
        if standalone:
            configuration = datasets.get_dataset_configuration(actor.id, key)
            dataset = configuration.dataset
            editable = configuration.may_maintain
            may_govern = configuration.may_govern
            may_edit_key = configuration.may_edit_key
            may_export = authorization.datasets.can_export_dataset(actor.id, dataset.id)
            context, state, identifier = None, None, None
            fields, field_states = (), {}
            try:
                field_view = dataset_versions.read(actor.id, key, page=None, prefer_concept=True)
            except NotFoundError:
                field_context_label = "No Current version or active Draft available."
            else:
                fields, identifier = field_view.fields, field_view.identifier
                overview, state_for_facts = field_view.context, field_view.state
                schema_admin = field_view.state is None and field_view.may_maintain
                may_start_concept = (
                    field_view.may_maintain
                    and field_view.concept is None
                    and field_view.state is not None
                    and field_view.state.status == "Current"
                )
                draft_in_progress = field_view.concept is not None
                if field_view.state:
                    field_context_label = (
                        f"Current · Version {field_view.state.version.number} · Read only"
                    )
                elif not schema_admin:
                    field_context_label = "Draft · Read only"
            if overview is None:
                schema_admin, may_start_concept = False, False
        else:
            may_edit_key = False
            try:
                view = dataset_versions.read(
                    actor.id, key, version_id, page=None, prefer_concept=concept
                )
            except NotFoundError:
                if version_id is not None or concept:
                    raise
                history = dataset_versions.history(actor.id, key)
                return render_authenticated_template(
                    "pages/datasets/no_current.html",
                    dataset=history.context.dataset,
                    history=history,
                )
            dataset = view.context.dataset
            editable = view.may_maintain
            schema_admin = view.state is None and view.may_maintain
            may_govern = editable and authorization.datasets.can_change_dataset_owner(
                actor.id, dataset.id
            )
            context, state, identifier = view.context, view.state, view.identifier
            overview, state_for_facts = view.context, view.state
            fields = view.fields
            field_states = {
                change.id: change.status
                for change in (g.concept_ui.field_changes if concept else ())
                if change.status != ChangeStatus.ADDED
            }
            may_start_concept = (
                view.may_maintain
                and view.concept is None
                and view.state is not None
                and view.state.status == "Current"
            )
            if editable:
                may_edit_key = datasets.get_dataset_configuration(actor.id, key).may_edit_key
            may_export = view.may_export
            if view.state is not None:
                field_context_label = (
                    f"{view.state.status} · Version {view.state.version.number} · Read only"
                )
            draft_in_progress = view.concept is not None
        # "At a glance" facts for the Dataset details tab.
        if overview is None:
            version_label = "No version available"
        elif state_for_facts is not None:
            version_label = f"Version {state_for_facts.version.number} · {state_for_facts.status}"
        else:
            version_label = "Draft in progress"
        dataset_facts = {
            "version": version_label,
            "record_count": overview.record_count if overview else None,
            "field_count": len(fields),
            "created_at": dataset.created_at,
            "last_updated": overview.last_updated if overview else None,
        }
        mapping_source_table = None
        if dataset.kind == "mapping" and dataset.source_dataset_id:
            try:
                visible_dataset_lookup = datasets.get_visible_dataset_by_id
                mapping_source_table = visible_dataset_lookup(actor.id, dataset.source_dataset_id)
            except NotFoundError:
                mapping_source_table = None
        mapping_incomplete_text = (
            mapping_incomplete_message(fields) if dataset.kind == "mapping" else None
        )
        if edit_mode and not editable:
            abort(404)
        # The Danger zone tab shows what a deletion would remove.
        deletion = datasets.get_dataset_for_deletion(actor.id, dataset.id) if editable else None
        access_teams, access_users, access_entries = [], [], []
        if may_govern:
            _, access_teams, access_users, access_entries = datasets.export_access_context(
                actor.id, key
            )
        return render_authenticated_template(
            "pages/datasets/detail.html",
            dataset=dataset,
            context=context,
            standalone_configuration=standalone,
            configuration_url=url_for("dataset_configuration", key=dataset.key),
            editable=editable,
            edit_mode=edit_mode,
            name=dataset.name if name is None else name,
            technical_key=request.form.get("technical_key", dataset.key),
            may_edit_key=may_edit_key,
            description=dataset.description if description is None else description,
            error=error,
            error_field=error_field,
            dataset_icon_choices=DATASET_ICONS,
            selected_icon=request.form.get("icon", dataset.icon),
            teams=access_teams,
            selected_team_id=request.form.get("owner_team_id", dataset.owner_team_id),
            expected_owner_team_id=request.form.get(
                "expected_owner_team_id", dataset.owner_team_id
            ),
            schema_admin=schema_admin,
            may_start_concept=may_start_concept,
            may_govern=may_govern,
            may_export=may_export,
            draft_in_progress=draft_in_progress,
            access_teams=access_teams,
            access_users=access_users,
            access_entries=access_entries,
            access_error=access_error,
            access_subject=access_subject,
            fields=fields,
            mapping_source_table=mapping_source_table,
            mapping_incomplete=bool(mapping_incomplete_text),
            mapping_incomplete_message=mapping_incomplete_text,
            field_states=field_states,
            field_context_label=field_context_label,
            dataset_facts=dataset_facts,
            deletion=deletion,
            dataset_icon_label=DATASET_ICONS.get(
                rendered_dataset_icon(dataset.icon, dataset.kind),
                rendered_dataset_icon(dataset.icon, dataset.kind).replace("-", " ").capitalize(),
            ),
            business_key=identifier,
            version_state=state,
        )

    @dataset_route(
        "/datasets/<key>/concept/new", capability="can_maintain_dataset", methods=["POST"]
    )
    @version_errors
    def dataset_concept_create(key):
        actor, session = require_authenticated()
        require_csrf(session.csrf_token)
        dataset = datasets.get_dataset_for_edit(actor.id, key)
        try:
            dataset_versions.create_concept(actor.id, dataset.id)
        except ConflictError as error:
            return render_authenticated_template(
                "pages/datasets/concept_required.html",
                key=key,
                error=str(error),
                already_active=True,
            ), 409
        return redirect(url_for("dataset_records_view", key=key, concept=True), code=303)

    @dataset_route(
        "/datasets/<key>/concept/discard",
        capability="can_maintain_dataset",
        methods=["GET", "POST"],
    )
    @version_errors
    @concept_route
    def dataset_concept_discard(key):
        actor, session = require_authenticated()
        dataset = datasets.get_dataset_for_edit(actor.id, key)
        error, status = None, 200
        if request.method == "POST":
            require_csrf(session.csrf_token)
            if set(request.form) != {"csrf_token", "confirmation", "confirmed"} or any(
                len(request.form.getlist(key)) != 1 for key in request.form
            ):
                return "Review the discard form again.", 400
            try:
                identity = decode_discard(request.form["confirmation"], session.id, review_secret)
            except ValueError:
                audit_security("security.workflow_token_rejected", actor_user_id=actor.id)
                raise NotFoundError("Concept not found") from None
            if identity.actor_id != actor.id or identity.dataset_id != dataset.id:
                audit_security("security.workflow_token_rejected", actor_user_id=actor.id)
                raise NotFoundError("Concept not found")
            try:
                if request.form["confirmed"] != "yes":
                    raise ValidationError("Confirm discard of this Concept.")
                result = dataset_versions.discard_concept(actor.id, dataset.id, identity)
                record_imports.store.revoke_concept(result.identity.concept_id)
                return redirect(url_for("dataset_detail", key=key, notice="discarded"), code=303)
            except (ConflictError, ValidationError) as failure:
                error, status = str(failure), 409 if isinstance(failure, ConflictError) else 400
        review, token = None, None
        try:
            review = dataset_versions.review_discard(actor.id, dataset.id)
            token = encode_discard(review.identity, session.id, review_secret)
        except ConflictError as failure:
            error, status = error or str(failure), 409
        return render_authenticated_template(
            "pages/datasets/concepts/discard.html",
            dataset=dataset,
            review=review,
            confirmation=token,
            error=error,
        ), status

    @dataset_route("/datasets/<key>/versions", capability="can_view_dataset", methods=["GET"])
    @version_errors
    def dataset_versions_view(key):
        actor, _session = require_authenticated()
        history = dataset_versions.history(actor.id, key)
        notice = {
            "published": "Dataset version published.",
            "cancelled": "Scheduled version cancelled.",
        }.get(request.args.get("notice"))
        return render_authenticated_template(
            "pages/datasets/versions/list.html",
            history=history,
            dataset=history.context.dataset,
            context=history.context,
            notice=notice,
            review_available=bool(history.concept_summary and history.concept_summary.can_review),
        )

    def review_error(key, message=STALE_MESSAGE, status=409):
        return render_authenticated_template(
            "pages/datasets/versions/review_error.html", key=key, error=message
        ), status

    def review_identity(actor, session, dataset):
        tokens = request.args.getlist("review")
        if len(tokens) != 1:
            audit_security("security.workflow_token_rejected", actor_user_id=actor.id)
            raise NotFoundError("Review not found")
        try:
            identity = decode_review(tokens[0], session.id, review_secret)
        except ValueError:
            audit_security("security.workflow_token_rejected", actor_user_id=actor.id)
            raise ConflictError("This review link is no longer valid. Reload the review.") from None
        if identity.dataset_id != dataset.id or identity.actor_id != actor.id:
            audit_security("security.workflow_token_rejected", actor_user_id=actor.id)
            raise NotFoundError("Review not found")
        return identity, tokens[0]

    def review_parameters(*, detail=False):
        allowed = {"review", "section", "q", "status", "anchor", "backwards"}
        if detail:
            allowed.add("detail_page")
        if (
            set(request.args) - allowed
            or len(request.query_string) > 4096
            or any(
                len(request.args.getlist(name)) != 1 for name in request.args if name != "status"
            )
        ):
            raise ValidationError("Choose valid review parameters.")
        section = request.args.get("section", "Summary")
        if section not in {"Summary", "Fields", "Records"} or request.args.get(
            "backwards", "0"
        ) not in {"0", "1"}:
            raise ValidationError("Choose valid review parameters.")
        try:
            statuses = tuple(change_status(value) for value in request.args.getlist("status"))
        except ValueError:
            raise ValidationError("Choose valid review parameters.") from None
        criteria = DiffCriteria(
            statuses or STATUSES,
            request.args.get("q", ""),
            request.args.get("anchor", ""),
            request.args.get("backwards", "0") == "1",
        )
        # Service revalidates every supplied criterion for trusted programmatic callers.
        dataset_diff._criteria(criteria)
        return section, criteria

    def review_url(key, token, section="Summary", criteria=None, **changes):
        criteria = criteria or DiffCriteria()
        values = dict(
            review=token,
            section=section,
            q=criteria.search,
            status=list(criteria.statuses),
            anchor=criteria.anchor,
            backwards="1" if criteria.backwards else "0",
        )
        values.update(changes)
        return url_for("dataset_version_review", key=key, **values)

    @dataset_route(
        "/datasets/<key>/versions/review", capability="can_view_dataset", methods=["GET"]
    )
    @version_errors
    @concept_route
    def dataset_version_review(key):
        actor, session = require_authenticated()
        dataset = datasets.get_dataset_for_edit(actor.id, key)
        try:
            if not request.args:
                opened = dataset_diff.open_review(actor.id, dataset.id)
                token = encode_review(opened.identity, session.id, review_secret)
                return redirect(review_url(key, token), code=303)
            identity, token = review_identity(actor, session, dataset)
            section, criteria = review_parameters()
            result = dataset_diff.review(actor.id, identity, section, criteria)
            return render_authenticated_template(
                "pages/datasets/versions/review.html",
                dataset=dataset,
                identity=identity,
                token=token,
                section=section,
                criteria=criteria,
                result=result,
                sections={
                    name: review_url(key, token, name) for name in ("Summary", "Fields", "Records")
                },
                page_url=lambda **values: review_url(key, token, section, criteria, **values),
                detail_url=lambda kind, object_id: url_for(
                    "dataset_version_record_diff"
                    if kind == "record"
                    else "dataset_version_field_diff",
                    key=key,
                    object_id=object_id,
                    review=token,
                    section=section,
                    q=criteria.search,
                    status=list(criteria.statuses),
                    anchor=criteria.anchor,
                    backwards="1" if criteria.backwards else "0",
                ),
            )
        except ConflictError as error:
            return review_error(key, str(error))
        except ValidationError as error:
            return review_error(key, str(error), 400)

    def render_diff_detail(key, object_id, kind):
        actor, session = require_authenticated()
        dataset = datasets.get_dataset_for_edit(actor.id, key)
        try:
            identity, token = review_identity(actor, session, dataset)
            section, criteria = review_parameters(detail=True)
            if kind == "record":
                raw_page = request.args.get("detail_page", "1")
                if len(raw_page) > 10 or not raw_page.isascii() or not raw_page.isdigit():
                    raise ValidationError("Choose a valid detail page.")
                item = dataset_diff.record_detail(actor.id, identity, object_id, int(raw_page))
            else:
                item = dataset_diff.field_detail(actor.id, identity, object_id)
            return render_authenticated_template(
                "pages/datasets/versions/diff_detail.html",
                dataset=dataset,
                identity=identity,
                kind=kind,
                item=item,
                return_url=review_url(key, token, section, criteria),
                detail_page_url=lambda number: url_for(
                    "dataset_version_record_diff",
                    key=key,
                    object_id=object_id,
                    review=token,
                    section=section,
                    q=criteria.search,
                    status=list(criteria.statuses),
                    anchor=criteria.anchor,
                    backwards="1" if criteria.backwards else "0",
                    detail_page=number,
                ),
            )
        except ConflictError as error:
            return review_error(key, str(error))
        except ValidationError as error:
            return review_error(key, str(error), 400)

    @dataset_route(
        "/datasets/<key>/versions/review/records/<object_id>",
        capability="can_view_dataset",
        methods=["GET"],
    )
    @version_errors
    @concept_route
    def dataset_version_record_diff(key, object_id):
        return render_diff_detail(key, object_id, "record")

    @dataset_route(
        "/datasets/<key>/versions/review/fields/<object_id>",
        capability="can_view_dataset",
        methods=["GET"],
    )
    @version_errors
    @concept_route
    def dataset_version_field_diff(key, object_id):
        return render_diff_detail(key, object_id, "field")

    @dataset_route(
        "/datasets/<key>/versions/publish",
        capability="can_maintain_dataset",
        methods=["GET", "POST"],
    )
    @version_errors
    @concept_route
    def dataset_version_publish(key):
        actor, session = require_authenticated()
        dataset = datasets.get_dataset_for_edit(actor.id, key)
        expected_review, review_token = None, None
        if request.method == "POST":
            require_csrf(session.csrf_token)
        if "review" in request.args:
            try:
                expected_review, review_token = review_identity(actor, session, dataset)
                dataset_diff.review(actor.id, expected_review)
            except ConflictError as failure:
                if request.method == "POST":
                    return redirect(review_url(key, request.args["review"]), code=303)
                return review_error(key, str(failure))
        error, status = None, 200
        effective = request.form.get("effective_date", "")[:10]
        note = request.form.get("release_note", "")[:2000]
        if request.method == "POST":
            if request.form.getlist("dataset_id") != [dataset.id]:
                raise NotFoundError("Dataset not found")
            try:
                if request.form.getlist("confirmed") != ["yes"]:
                    raise ValidationError(
                        "Confirm that you reviewed this version before publishing."
                    )
                dates = request.form.getlist("effective_date")
                if len(dates) != 1 or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", dates[0]) is None:
                    raise ValidationError("Choose an effective calendar date.")
                try:
                    effective_date = date.fromisoformat(dates[0])
                except ValueError:
                    raise ValidationError("Choose a valid effective calendar date.") from None
                revisions = request.form.getlist("expected_revision")
                if len(revisions) != 1 or len(request.form.getlist("release_note")) != 1:
                    raise ValidationError("Review the publication form again.")
                expected = parse_expected_revision(revisions[0])
                dataset_versions.publish(
                    actor.id,
                    dataset.id,
                    effective_date,
                    request.form["release_note"],
                    expected,
                    expected_review=expected_review,
                )
                return redirect(
                    url_for("dataset_versions_view", key=key, notice="published"), code=303
                )
            except (ValidationError, ConflictError) as failure:
                if expected_review is not None and isinstance(failure, ConflictError):
                    return redirect(review_url(key, review_token), code=303)
                error, status = str(failure), 409 if isinstance(failure, ConflictError) else 400
        review = None
        try:
            review = dataset_versions.review(actor.id, dataset.id)
            if expected_review is not None and review[0] != expected_review.revision:
                return review_error(key)
        except (ValidationError, ConflictError) as failure:
            error, status = error or str(failure), 409
        today = dataset_versions.clock.today().isoformat()
        concept_summary = dataset_versions.history(actor.id, key).concept_summary
        return render_authenticated_template(
            "pages/datasets/versions/publish.html",
            dataset=dataset,
            review=review,
            error=error,
            effective_date=effective or today,
            release_note=note,
            today=today,
            review_token=review_token,
            review_available=bool(review_token)
            or bool(concept_summary and concept_summary.can_review),
        ), status

    @dataset_route(
        "/datasets/<key>/versions/<version_id>/cancel",
        capability="can_maintain_dataset",
        methods=["GET", "POST"],
    )
    @version_errors
    def dataset_version_cancel(key, version_id):
        actor, session = require_authenticated()
        dataset = datasets.get_dataset_for_edit(actor.id, key)
        error, status = None, 200
        if request.method == "POST":
            require_csrf(session.csrf_token)
            try:
                if request.form.getlist("confirmed") != ["yes"]:
                    raise ValidationError("Confirm cancellation of this Scheduled version.")
                dataset_versions.cancel(actor.id, dataset.id, version_id)
                return redirect(
                    url_for("dataset_versions_view", key=key, notice="cancelled"), code=303
                )
            except (ValidationError, ConflictError) as failure:
                error, status = str(failure), 409 if isinstance(failure, ConflictError) else 400
        history = dataset_versions.history(actor.id, key)
        selected = next(
            (state for state in history.entries if state.version.id == version_id), None
        )
        if selected is None:
            raise NotFoundError("Dataset version not found")
        if selected.status != "Scheduled":
            error, status = (
                "Only a Scheduled version can be cancelled before its effective date.",
                409,
            )
        return render_authenticated_template(
            "pages/datasets/versions/cancel.html",
            dataset=dataset,
            state=selected,
            error=error,
            can_cancel=selected.status == "Scheduled",
        ), status

    def access_management_target(key):
        """Where export access changes return: the configuration page they came from."""
        standalone = request.args.get("standalone") == "1"
        concept = request.args.get("concept") == "1"
        endpoint = "dataset_configuration" if standalone else "dataset_detail"
        options = {} if standalone else {"concept": concept or None}
        return (
            standalone,
            concept,
            url_for(endpoint, key=key, _anchor="access-management", **options),
        )

    @dataset_route(
        "/datasets/<key>/export-access", capability="can_manage_export_access", methods=["POST"]
    )
    @dataset_not_found_route
    def dataset_export_access(key):
        actor, login_session = require_authenticated()
        _auth, service, *_rest = require_dataset_dependencies()
        require_csrf(login_session.csrf_token)
        standalone, concept, target = access_management_target(key)
        try:
            expected = {"csrf_token", "subject"}
            if set(request.form) != expected or any(
                len(request.form.getlist(item)) != 1 for item in expected
            ):
                raise ValidationError("Choose one user or team.")
            subject_type, separator, subject_id = request.form["subject"].partition(":")
            if not separator:
                raise ValidationError("Choose one user or team.")
            service.grant_export_access(actor.id, key, subject_type, subject_id)
        except (ValidationError, ConflictError) as error:
            # Show the error on the Access management tab the form came from.
            if concept:
                try:
                    g.concept_ui = dataset_versions.concept_context(actor.id, key)
                except (NotFoundError, ConflictError):
                    concept = False
            response = render_dataset_configuration(
                key,
                concept=concept,
                standalone=standalone,
                access_error=str(error),
                access_subject=request.form.get("subject", ""),
            )
            return response, 409 if isinstance(error, ConflictError) else 400
        return redirect(target)

    @dataset_route(
        "/datasets/<key>/export-access/<access_id>/revoke",
        capability="can_manage_export_access",
        methods=["POST"],
    )
    @dataset_not_found_route
    def revoke_dataset_export_access(key, access_id):
        actor, login_session = require_authenticated()
        _auth, service, *_rest = require_dataset_dependencies()
        require_csrf(login_session.csrf_token)
        service.revoke_export_access(actor.id, key, access_id)
        return redirect(access_management_target(key)[2])

    @dataset_route(
        "/datasets/<key>/fields/order", capability="can_maintain_dataset", methods=["POST"]
    )
    @concept_route
    def move_dataset_field(key: str):
        actor, login_session = require_authenticated()
        _authorization, _datasets, field_service, _business_keys, _records = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        if set(request.form) == {"csrf_token", "expected_field_id", "ordered_field_id"}:
            # Drag and keyboard reordering (field-order.js) submit the complete new order.
            expected = tuple(request.form.getlist("expected_field_id"))
            proposed = tuple(request.form.getlist("ordered_field_id"))
            try:
                field_service.replace_order(actor.id, key, expected, proposed)
            except NotFoundError:
                abort(404)
            except ValidationError as error:
                abort(400, description=str(error))
            except StaleFieldOrderError as error:
                abort(409, description=str(error))
            return "", 204
        if set(request.form) != {"csrf_token", "expected_field_id", "field_id", "direction"}:
            abort(400, description="Invalid field order request")
        field_ids = request.form.getlist("field_id")
        directions = request.form.getlist("direction")
        expected = tuple(request.form.getlist("expected_field_id"))
        if len(field_ids) != 1 or len(directions) != 1 or not expected:
            abort(400, description="Invalid field order request")
        try:
            field_service.move_field(actor.id, key, field_ids[0], directions[0], expected)
        except NotFoundError:
            abort(404)
        except ValidationError as error:
            abort(400, description=str(error))
        except StaleFieldOrderError as error:
            abort(409, description=str(error))
        return redirect(
            url_for("dataset_detail", key=key, concept=True, tab="fields", _anchor="fields")
        )

    def parse_page_parameter() -> int:
        values = request.args.getlist("page")
        if not values:
            return 1
        if len(values) != 1 or re.fullmatch(r"[1-9][0-9]{0,8}", values[0]) is None:
            abort(400, description="Page must be a canonical positive integer")
        return int(values[0])

    def hierarchy_presentation(view):
        """Arrange a snapshot for templates without changing its structure or values."""
        nodes = {node.id: node for node in view.hierarchy_nodes}
        children = {}
        for node in nodes.values():
            children.setdefault(node.parent_id, []).append(node)
        for siblings in children.values():
            siblings.sort(key=lambda node: (node.name.casefold(), node.id))
        rows, visited = [], set()

        def append_subtree(start):
            pending = [(start, ())]
            while pending:
                node, ancestors = pending.pop()
                if node.id in visited:
                    continue
                visited.add(node.id)
                ancestor_names = tuple(row["node"].name for row in ancestors)
                path = (*ancestor_names, node.name)
                rows.append(
                    {
                        "node": node,
                        "depth": len(ancestors),
                        "ancestors": ancestor_names,
                        "ancestor_rows": ancestors,
                        "parent_name": nodes[node.parent_id].name
                        if node.parent_id in nodes
                        else None,
                        "path": " / ".join(path),
                        "leaf": not children.get(node.id),
                        "values": {
                            value.field_key: value.value
                            for value in node.values
                            if any(
                                field.id == value.field_id and field.data_type == value.data_type
                                for field in view.fields
                            )
                        },
                    }
                )
                pending.extend(
                    (child, (*ancestors, rows[-1])) for child in reversed(children.get(node.id, ()))
                )

        for node in children.get(None, ()):
            append_subtree(node)
        # Invalid disconnected structure remains visible while its validation is resolved.
        for node in nodes.values():
            if node.id not in visited:
                append_subtree(node)
        return rows, children

    def render_hierarchy(view, actor_id):
        selected = request.args.getlist("view") or ["tree"]
        if len(selected) != 1 or selected[0] not in {
            "tree",
            "table",
            "hierarchy",
            "records",
            "hybrid",
        }:
            abort(400, description="Choose Tree or Table.")
        legacy_view = selected[0]
        selected[0] = {"hierarchy": "tree", "hybrid": "tree", "records": "table"}.get(
            selected[0], selected[0]
        )
        records_view = request.args.getlist("records_view") or ["reference"]
        if len(records_view) != 1 or records_view[0] not in {"reference", "flattened"}:
            abort(400, description="Choose a valid hierarchy view.")
        searches = request.args.getlist("q")
        sorts = request.args.getlist("sort") or [""]
        directions = request.args.getlist("direction") or ["asc"]
        if len(searches) > 1 or len(sorts) != 1 or len(directions) != 1:
            abort(400, description="Unsupported hierarchy view parameters.")
        if sorts[0] not in {"", "name", "parent", "key"} or directions[0] not in {"asc", "desc"}:
            abort(400, description="Unsupported hierarchy sort.")
        if set(request.args) - {"view", "records_view", "q", "sort", "direction"}:
            abort(400, description="Unsupported hierarchy view parameters.")
        if legacy_view != selected[0]:
            target = url_for(
                "dataset_records_view",
                key=view.context.dataset.key,
                version_id=view.state.version.id if view.state else None,
                concept=True if view.state is None else None,
                view=selected[0],
                q=searches[0] if searches else None,
            )
            return redirect(target, code=301)
        hierarchy_search = search_term(searches[0] if searches else "")
        normalized_search = hierarchy_search.casefold()
        rows, children = hierarchy_presentation(view)
        leaf_rows = [row for row in rows if row["leaf"] and row["node"].parent_id is not None]
        parent_level_count = max((len(row["ancestors"]) for row in leaf_rows), default=0)
        flat_value_columns = []
        for field in view.fields:
            if field.hierarchy_scope == "all_nodes":
                flat_value_columns.extend(
                    {"field": field, "label": f"{field.name} Level {level + 1}", "level": level}
                    for level in range(parent_level_count)
                )
            flat_value_columns.append(
                {"field": field, "label": f"{field.name} Leaf", "level": None}
            )

        def row_matches(row):
            return matches(row["node"], view.fields, normalized_search) or (
                normalized_search in row["node"].name.casefold()
            )

        if selected[0] == "table":
            unfiltered_rows = rows
            display_rows = [
                row for row in unfiltered_rows if not normalized_search or row_matches(row)
            ]
        else:
            unfiltered_rows = rows
            if normalized_search:
                matched_rows = [row for row in rows if row_matches(row)]
                visible_ids = {
                    candidate["node"].id
                    for row in matched_rows
                    for candidate in (*row["ancestor_rows"], row)
                }
                display_rows = [row for row in rows if row["node"].id in visible_ids]
            else:
                display_rows = rows

        key_field_key = (getattr(view.identifier, "field_keys", ()) or (None,))[0]
        key_field = next((field for field in view.fields if field.key == key_field_key), None)
        sort_key = sorts[0]
        if selected[0] == "table" and sort_key:

            def row_sort_value(row):
                if sort_key == "name":
                    return (1, row["node"].name.casefold())
                if sort_key == "parent":
                    return (1, (row["parent_name"] or "").casefold())
                value = row["values"].get(key_field.key) if key_field else None
                if value is None:
                    return (0, "")
                if key_field and key_field.data_type in {"integer", "number", "float"}:
                    return (1, value)
                return (2, str(value).casefold())

            display_rows = sorted(
                display_rows,
                key=lambda row: (row_sort_value(row), row["path"].casefold()),
                reverse=directions[0] == "desc",
            )

        hierarchy_clear_url = url_for(
            "dataset_records_view",
            key=view.context.dataset.key,
            version_id=view.state.version.id if view.state else None,
            concept=True if view.state is None else None,
            view=selected[0],
            sort=sorts[0] or None,
            direction=directions[0] if sorts[0] else None,
        )
        return render_authenticated_template(
            "pages/datasets/hierarchy.html",
            dataset=view.context.dataset,
            context=view.context,
            fields=view.fields,
            identifier=view.identifier,
            hierarchy_rows=rows,
            hierarchy_errors=view.hierarchy_errors,
            leaf_rows=leaf_rows,
            parent_level_count=parent_level_count,
            flat_value_columns=flat_value_columns,
            records_view=records_view[0],
            display_rows=display_rows,
            hierarchy_result_total=len(unfiltered_rows),
            leaf_count=len(leaf_rows),
            hierarchy_search=hierarchy_search,
            hierarchy_clear_url=hierarchy_clear_url,
            selected_view=selected[0],
            sort_key=sort_key,
            sort_direction=directions[0],
            key_field_key=key_field_key,
            default_column_keys=(
                [key_field_key]
                + [field.key for field in view.fields if field.key != key_field_key][
                    : 6 - bool(key_field_key)
                ]
                if key_field_key
                else [field.key for field in view.fields[:6]]
            ),
            version_state=view.state,
            may_export=view.may_export,
            may_edit=view.state is None and view.may_maintain,
            may_govern=authorization.datasets.can_change_dataset_owner(
                actor_id, view.context.dataset.id
            ),
            draft_in_progress=view.concept is not None,
            may_start_concept=(
                view.may_maintain
                and view.concept is None
                and view.state is not None
                and view.state.status == "Current"
            ),
        )

    def hierarchy_edit_context(key, node_id=None):
        actor, _session = require_authenticated()
        view = dataset_versions.read(actor.id, key, page=None, prefer_concept=True)
        if view.context.dataset.kind != "hierarchy" or view.state or not view.may_maintain:
            abort(404)
        rows, _children = hierarchy_presentation(view)
        row = next((row for row in rows if row["node"].id == node_id), None)
        if node_id is not None and row is None:
            abort(404)
        return actor, view, rows, row

    def render_hierarchy_node_form(
        view, rows, row=None, *, mode="structure", error=None, errors=None
    ):
        node = row["node"] if row else None
        node_ids = {entry["node"].id for entry in rows}
        excluded = {node.id} if node else set()
        if node:
            for _ in rows:
                descendants = {
                    entry["node"].id for entry in rows if entry["node"].parent_id in excluded
                }
                if descendants <= excluded:
                    break
                excluded.update(descendants)
        applicable = tuple(
            field
            for field in view.fields
            if row and (row["leaf"] or field.hierarchy_scope == "all_nodes")
        )
        parent_id = (
            request.form.get(
                "parent_id", request.args.get("parent_id", node.parent_id if node else "")
            )
            or None
        )
        if not node and rows and parent_id is None and request.method == "GET":
            parent_id = rows[0]["node"].id
        if parent_id is not None and parent_id not in node_ids:
            abort(404)
        return_view, return_q, return_sort, return_direction = hierarchy_return_view()
        return render_authenticated_template(
            "pages/datasets/hierarchy/node_form.html",
            dataset=view.context.dataset,
            node=node,
            node_row=row,
            mode=mode,
            fields=applicable,
            inactive_fields=tuple(
                field for field in view.fields if field not in applicable and row
            ),
            parent_options=[entry for entry in rows if entry["node"].id not in excluded],
            parent_id=parent_id,
            name=request.form.get("name", node.name if node else ""),
            values=(
                {
                    key.removeprefix("field:"): value
                    for key, value in request.form.items()
                    if key.startswith("field:")
                }
                if request.method == "POST"
                else (row["values"] if row else {})
            ),
            raw_values=request.method == "POST",
            expected_revision=request.form.get("expected_revision", view.context.dataset.revision),
            return_view=return_view,
            return_q=return_q,
            return_sort=return_sort,
            return_direction=return_direction,
            error=error,
            errors=errors or {},
        )

    def hierarchy_return_view():
        values = request.form if request.method == "POST" else request.args
        selected = values.getlist("return_view") or values.getlist("view") or ["tree"]
        selected[0] = {"hierarchy": "tree", "hybrid": "tree", "records": "table"}.get(
            selected[0], selected[0]
        )
        searches = values.getlist("return_q") or values.getlist("q") or [""]
        sorts = values.getlist("return_sort") or values.getlist("sort") or [""]
        directions = values.getlist("return_direction") or values.getlist("direction") or ["asc"]
        legacy_records = (
            values.getlist("return_records_view") or values.getlist("records_view") or ["reference"]
        )
        if (
            len(selected) != 1
            or selected[0] not in {"tree", "table"}
            or len(searches) != 1
            or len(sorts) != 1
            or sorts[0] not in {"", "name", "parent", "key"}
            or len(directions) != 1
            or directions[0] not in {"asc", "desc"}
            or len(legacy_records) != 1
            or legacy_records[0] not in {"reference", "flattened"}
        ):
            abort(400, description="Choose a valid return view.")
        return selected[0], searches[0], sorts[0], directions[0]

    @dataset_route(
        "/datasets/<key>/concept/nodes/new",
        capability="can_maintain_dataset",
        methods=["GET", "POST"],
    )
    @version_errors
    @concept_route
    def dataset_hierarchy_node_create(key):
        actor, view, rows, _row = hierarchy_edit_context(key)
        if request.method == "POST":
            try:
                hierarchy.create_node(
                    actor.id,
                    key,
                    request.form.get("name", ""),
                    request.form.get("parent_id") or None,
                    expected_revision=exact_expected_revision(),
                )
            except (ValidationError, ConflictError) as error:
                return render_hierarchy_node_form(view, rows, error=str(error)), 409 if isinstance(
                    error, ConflictError
                ) else 400
            return redirect(url_for("dataset_records_view", key=key, concept=True))
        return render_hierarchy_node_form(view, rows)

    @dataset_route(
        "/datasets/<key>/concept/nodes/<node_id>/edit",
        capability="can_maintain_dataset",
        methods=["GET", "POST"],
    )
    @version_errors
    @concept_route
    def dataset_hierarchy_node_edit(key, node_id):
        actor, view, rows, row = hierarchy_edit_context(key, node_id)
        if request.method == "POST":
            submitted = {}
            if any(
                form_key.startswith("field:") and len(form_values) != 1
                for form_key, form_values in request.form.lists()
            ):
                abort(400, description="Submit exactly one value per field.")
            for form_key, form_values in request.form.lists():
                if form_key.startswith("field:"):
                    submitted[form_key.removeprefix("field:")] = form_values[0]
            try:
                hierarchy.update_node_and_values(
                    actor.id,
                    key,
                    node_id,
                    request.form.get("name", ""),
                    request.form.get("parent_id") or None,
                    submitted,
                    expected_revision=exact_expected_revision(),
                )
            except (ValidationError, ConflictError) as error:
                return render_hierarchy_node_form(
                    view, rows, row, error=str(error)
                ), 409 if isinstance(error, ConflictError) else 400
            return redirect(
                url_for(
                    "dataset_records_view",
                    key=key,
                    concept=True,
                    view=hierarchy_return_view()[0],
                    q=hierarchy_return_view()[1] or None,
                    sort=hierarchy_return_view()[2] or None,
                    direction=hierarchy_return_view()[3] if hierarchy_return_view()[2] else None,
                )
            )
        return render_hierarchy_node_form(view, rows, row)

    @dataset_route(
        "/datasets/<key>/concept/nodes/<node_id>/delete",
        capability="can_maintain_dataset",
        methods=["GET", "POST"],
    )
    @version_errors
    @concept_route
    def dataset_hierarchy_node_delete(key, node_id):
        actor, view, rows, row = hierarchy_edit_context(key, node_id)
        if row["node"].parent_id is None:
            abort(400, description="The root node cannot be deleted.")
        subtree_count = 1 + sum(
            any(ancestor["node"].id == node_id for ancestor in entry["ancestor_rows"])
            for entry in rows
            if entry["node"].id != node_id
        )
        error, status = None, 200
        if request.method == "POST":
            try:
                if set(request.form) - {"csrf_token", "confirm", "expected_revision"}:
                    raise ValidationError("Unknown form field")
                if request.form.getlist("confirm") != ["yes"]:
                    raise ValidationError("Explicit confirmation is required")
                hierarchy.delete_subtree(
                    actor.id,
                    key,
                    node_id,
                    expected_revision=exact_expected_revision(),
                )
            except (ValidationError, ConflictError) as failure:
                error = str(failure)
                status = 409 if isinstance(failure, ConflictError) else 400
            else:
                return redirect(url_for("dataset_records_view", key=key, concept=True))
        response = render_authenticated_template(
            "pages/datasets/hierarchy/delete.html",
            dataset=view.context.dataset,
            node_row=row,
            subtree_count=subtree_count,
            expected_revision=request.form.get("expected_revision", view.context.dataset.revision),
            error=error,
        )
        return (response, status) if status != 200 else response

    @dataset_route(
        "/datasets/<key>/concept/nodes/<node_id>/values",
        capability="can_view_dataset",
        methods=["GET", "POST"],
    )
    @version_errors
    @concept_route
    def dataset_hierarchy_node_values(key, node_id):
        actor, view, rows, row = hierarchy_edit_context(key, node_id)
        return_view, return_q, return_sort, return_direction = hierarchy_return_view()
        if request.method == "POST":
            applicable = [
                field
                for field in view.fields
                if row["leaf"] or field.hierarchy_scope == "all_nodes"
            ]
            submitted, errors = {}, {}
            expected_fields = {f"field:{field.key}" for field in applicable}
            for form_key, form_values in request.form.lists():
                if form_key in {
                    "csrf_token",
                    "expected_revision",
                    "return_view",
                    "return_q",
                    "return_sort",
                    "return_direction",
                    "return_records_view",
                }:
                    continue
                field_key = form_key.removeprefix("field:")
                if form_key not in expected_fields:
                    errors[field_key] = "This field does not apply to the selected node."
                elif len(form_values) != 1:
                    errors[field_key] = "Submit exactly one value."
                else:
                    submitted[field_key] = form_values[0]
            try:
                if errors:
                    raise RecordValidationError(errors)
                hierarchy.update_values(
                    actor.id,
                    key,
                    node_id,
                    submitted,
                    expected_revision=exact_expected_revision(),
                )
            except (ValidationError, ConflictError) as error:
                return render_hierarchy_node_form(
                    view,
                    rows,
                    row,
                    mode="values",
                    error=str(error),
                    errors=getattr(error, "errors", {}),
                ), 409 if isinstance(error, ConflictError) else 400
            return redirect(
                url_for(
                    "dataset_records_view",
                    key=key,
                    concept=True,
                    view=return_view,
                    q=return_q or None,
                    sort=return_sort or None,
                    direction=return_direction if return_sort else None,
                )
            )
        return render_hierarchy_node_form(view, rows, row, mode="values")

    @dataset_route(
        "/datasets/<key>/concept/records",
        capability="can_view_dataset",
        methods=["GET"],
        defaults={"concept": True},
    )
    @dataset_route(
        "/datasets/<key>/records",
        capability="can_view_dataset",
        methods=["GET"],
        defaults={"concept": False},
    )
    @dataset_route(
        "/datasets/<key>/versions/<version_id>/records",
        capability="can_view_dataset",
        methods=["GET"],
        defaults={"concept": False},
    )
    @version_errors
    @no_store_route
    def dataset_records_view(key: str, version_id=None, concept=False):
        actor, login_session = require_authenticated()
        require_dataset_dependencies()
        try:
            if len(request.query_string) > MAX_QUERY_BYTES:
                raise RecordSearchError(
                    "Record criteria exceed the URL limit. Clear all to start again."
                )
            if datasets.get_dataset(actor.id, key).kind == "hierarchy":
                return render_hierarchy(
                    dataset_versions.read(
                        actor.id, key, version_id, page=None, prefer_concept=concept
                    ),
                    actor.id,
                )
            parameters, selected_statuses, record_status, requested_page = parse_parameters(
                request.args.items(multi=True)
            )
            if not concept and any(
                name in parameters for name in ("import_results", "status_selection")
            ):
                raise RecordSearchError("Import results require a Concept.")
            if not concept and record_status != "all":
                raise RecordSearchError("Change filters are available only in a Concept.")
            import_results = (
                concept
                and parameters.get("import_results") == "1"
                and (parameters.get("status_selection") != "1" or bool(selected_statuses))
            )
            action = parameters.get("action", "")
            view = dataset_versions.read(
                actor.id,
                key,
                version_id,
                1 if action else requested_page,
                prefer_concept=concept,
                search=parameters.get("q", ""),
                statuses=(selected_statuses or DISPLAY_STATUSES) if import_results else (),
                record_status=record_status,
            )
            criteria_parameters = {
                "q": view.search or None,
                "record_status": record_status if concept and record_status != "all" else None,
            }
            if import_results:
                criteria_parameters.update(import_results=1, status=selected_statuses)
            if action:
                return redirect(
                    url_for(
                        "dataset_records_view",
                        key=key,
                        version_id=version_id,
                        concept=concept or None,
                        **criteria_parameters,
                    ),
                    code=303,
                )
        except RecordSearchError as error:
            # Neutral errors neither include submitted values nor render results
            # from a partially accepted query. The reset URL carries no criteria.
            return render_authenticated_template(
                "pages/datasets/search_error.html",
                error=str(error),
                clear_url=url_for(
                    "dataset_records_view", key=key, version_id=version_id, concept=concept or None
                ),
            ), 400
        except NotFoundError:
            if version_id is not None or concept:
                raise
            history = dataset_versions.history(actor.id, key)
            return render_authenticated_template(
                "pages/datasets/no_current.html", dataset=history.context.dataset, history=history
            )

        context, fields, identifier, page = view.context, view.fields, view.identifier, view.page
        dataset = context.dataset
        may_edit = may_configure = view.state is None and view.may_maintain
        mapping_incomplete_text = (
            mapping_incomplete_message(fields) if dataset.kind == "mapping" else None
        )
        may_add_record = (
            may_edit
            and mapping_incomplete_text is None
            and not (dataset.kind == "mapping" and dataset.mapping_mode == "linked")
        )
        rows = [
            (
                record,
                {
                    field.key: value.value
                    for field in fields
                    for value in record.values
                    if field.id == value.field_id and field.data_type == value.data_type
                },
                any(
                    key not in {value.field_key for value in record.values}
                    for key in identifier.field_keys
                ),
            )
            for record in page.records
        ]
        numeric_scales = {
            field.key: max(
                (
                    max(0, -value.as_tuple().exponent)
                    for _record, values, _incomplete in rows
                    if isinstance((value := values.get(field.key)), Decimal)
                ),
                default=0,
            )
            for field in fields
            if field.data_type == "number"
        }

        def display_record_value(field, value):
            if field.data_type == "number" and isinstance(value, Decimal):
                return (
                    f"{value:.{numeric_scales.get(field.key, max(0, -value.as_tuple().exponent))}f}"
                )
            return get_datatype(field.data_type).text(value) if value is not None else None

        record_type = key[:-3] + "y" if key.endswith("ies") else key.removesuffix("s")
        range_start = (page.page - 1) * page.page_size + 1 if page.records else 0
        range_end = min(page.page * page.page_size, page.total) if page.records else 0

        def record_page_url(number):
            return url_for(
                "dataset_records_view",
                key=key,
                version_id=view.state.version.id if view.state else None,
                concept=concept or None,
                page=number,
                **criteria_parameters,
            )

        change_order = tuple(RECORD_STATUS_FILTERS)
        selected_changes = set() if record_status == "all" else set(record_status.split(","))
        record_change_options = []
        for label, value, count in zip(
            DISPLAY_STATUSES,
            change_order,
            view.status_counts,
            strict=True,
        ):
            target_changes = selected_changes ^ {value}
            target = (
                ",".join(change for change in change_order if change in target_changes) or "all"
            )
            record_change_options.append((label, count, value in selected_changes, target))

        return render_template(
            "pages/datasets/records.html",
            dataset=dataset,
            context=context,
            fields=fields,
            identifier=identifier,
            rows=rows,
            page=page,
            criteria_parameters=criteria_parameters,
            record_status=record_status,
            record_page_url=record_page_url,
            clear_url=url_for(
                "dataset_records_view", key=key, version_id=version_id, concept=concept or None
            ),
            clear_import_url=url_for(
                "dataset_records_view",
                key=key,
                concept=concept or None,
                q=view.search or None,
                record_status="all",
            ),
            has_criteria=bool(view.search or import_results or record_status != "all"),
            import_unavailable=record_imports.eligibility(actor.id, key) if may_edit else None,
            import_results=import_results,
            import_totals=view.concept.import_result if view.concept and concept else None,
            result_statuses=selected_statuses,
            may_edit=may_edit,
            may_add_record=may_add_record,
            may_export=view.may_export,
            version_state=view.state,
            may_govern=authorization.datasets.can_change_dataset_owner(actor.id, dataset.id),
            draft_in_progress=view.concept is not None,
            may_configure=may_configure,
            may_start_concept=(
                view.may_maintain
                and view.concept is None
                and view.state is not None
                and view.state.status == "Current"
            ),
            record_states=view.record_states or {},
            record_labels=view.record_labels or {},
            record_details={
                record.id: tuple(
                    (field, next((v.value for v in record.values if v.field_id == field.id), None))
                    for field in (
                        view.deleted_fields.get(record.id, view.baseline_fields)
                        if view.record_states.get(record.id) == "Deleted"
                        else fields
                    )
                )
                for record in page.records
            },
            deleted_count=view.deleted_count,
            status_counts=view.status_counts,
            record_change_options=tuple(record_change_options),
            record_search=view.search,
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
            record_type=record_type,
            range_start=range_start,
            range_end=range_end,
            display_record_value=display_record_value,
        )

    @dataset_route(
        "/datasets/<key>/concept/records/export",
        capability="can_export_dataset",
        methods=["GET"],
        defaults={"concept": True},
    )
    @dataset_route(
        "/datasets/<key>/records/export",
        capability="can_export_dataset",
        methods=["GET"],
        defaults={"concept": False},
    )
    @dataset_route(
        "/datasets/<key>/versions/<version_id>/records/export",
        capability="can_export_dataset",
        methods=["GET"],
        defaults={"concept": False},
    )
    @version_errors
    def export_dataset_records(key: str, version_id=None, concept=False):
        actor, _login_session = require_authenticated()
        _authorization, _datasets, _fields, _business_keys, record_service = (
            require_dataset_dependencies()
        )
        formats = request.args.getlist("format")
        if len(formats) != 1 or formats[0] not in {"csv", "csv_zip", "xlsx", "json"}:
            return "Choose exactly one supported export format", 400
        variants = request.args.getlist("variant")
        if len(variants) > 1 or (
            variants and variants[0] not in {"data", "flattened", "reporting"}
        ):
            return "Choose Data, Flattened or Reporting for a hierarchy export", 400
        try:
            artifact = record_service.export_dataset(
                actor.id,
                key,
                formats[0],
                version_id=version_id,
                prefer_concept=concept,
                variant=variants[0] if variants else None,
            )
        except NotFoundError:
            abort(404)
        except (ExportGenerationError, ValidationError) as error:
            return (
                render_authenticated_template(
                    "pages/datasets/export_error.html",
                    error=str(error),
                    return_url=url_for(
                        "dataset_records_view",
                        key=key,
                        version_id=version_id,
                        concept=concept or None,
                    ),
                ),
                422,
            )
        try:
            response = send_file(
                artifact.content,
                mimetype=artifact.media_type,
                as_attachment=True,
                download_name=artifact.download_name or f"{key}.{artifact.extension}",
                max_age=0,
            )
        except Exception:
            logging.getLogger(__name__).exception("Dataset export stream cleanup failed")
            artifact.content.close()
            raise
        response.response = logged_export_stream(response.response)
        response.call_on_close(artifact.content.close)
        return response

    def import_route(rule, *, capability: str, **options):
        def decorate(function):
            function.required_capability = capability
            if getattr(function, "_secured_import", False):
                return dataset_route(rule, capability=capability, **options)(function)

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
                    return (
                        render_authenticated_template(
                            "pages/error.html",
                            error_code=403,
                            error_message="The import is not available to this account.",
                        ),
                        403,
                    )
                except ImportExpiredError:
                    resolved = getattr(g, "authenticated_resolution", None)
                    if resolved is not None:
                        audit_security(
                            "security.workflow_token_rejected",
                            actor_user_id=resolved[0].id,
                        )
                    minutes = max(1, (record_imports.store.ttl + 59) // 60)
                    return render_authenticated_template(
                        "pages/datasets/import/unavailable.html",
                        error=(
                            f"This import expired after {minutes} minutes without activity. "
                            "Upload the file again to continue."
                        ),
                        return_url=url_for("import_dataset_records", key=kwargs.get("key", "")),
                    ), 410
                except NotFoundError:
                    resolved = getattr(g, "authenticated_resolution", None)
                    if resolved is not None:
                        audit_security(
                            "security.workflow_token_rejected",
                            actor_user_id=resolved[0].id,
                        )
                    return (
                        render_authenticated_template(
                            "pages/error.html",
                            error_code=404,
                            error_message=(
                                "The import has expired or is unavailable. Upload the file again."
                            ),
                        ),
                        404,
                    )
                except ImportValidationError as error:
                    return render_authenticated_template(
                        "pages/datasets/import/unavailable.html", error=str(error)
                    ), 400

            secured_import._secured_import = True
            return dataset_route(rule, capability=capability, **options)(secured_import)

        return decorate

    def render_import_wizard(
        dataset,
        login_session,
        state=None,
        *,
        step=1,
        error=None,
        status=200,
        selected_mapping=None,
        source_format_issues=(),
    ):
        actor, _session = require_authenticated()
        mode = state.mode if state else "new_version"
        fields = ()
        source = assistance = preview = None
        required_keys = ()
        identifier_keys = ()
        mapping_errors = {}
        step_mapping_errors = []
        mapping_samples = []
        format_columns = []
        if state and state.imported_count is None:
            if step >= 3:
                state, source, assistance = record_imports.mapping_assistance(
                    actor.id,
                    login_session.id,
                    dataset.key,
                    state.token,
                    selected_mapping,
                    include_formats=True,
                )
                fields = assistance.fields
                required_keys = assistance.required_keys
                identifier_keys = assistance.identifier_keys
                if error or source_format_issues:
                    for issue in assistance.errors:
                        message = mapping_issue_message(issue, mode)
                        if issue.column_index is None:
                            if message:
                                step_mapping_errors.append(message)
                        elif message:
                            mapping_errors[issue.column_index] = message
            else:
                source = parse_source(state.filename, state.content, state.worksheet)
            if step >= 4:
                try:
                    preview = record_imports.saved_preview(
                        actor.id,
                        login_session.id,
                        dataset.key,
                        state.token,
                        page=parse_page_parameter(),
                        statuses=tuple(value for value in request.args.getlist("status") if value),
                        search=request.args.get("q", ""),
                    )
                except (NotFoundError, ImportValidationError):
                    raise
                except Exception:
                    logging.getLogger(__name__).exception("Import preview validation failed")
                    return (
                        "Validation could not be completed. No records have been imported. "
                        "Try again.",
                        500,
                    )
                state = record_imports.state(actor.id, login_session.id, dataset.key, state.token)
                if preview.non_row_errors:
                    step = 3
                    preview_messages = [
                        mapping_issue_message(item, mode) or item.message
                        for item in preview.non_row_errors
                    ]
                    error = " ".join(dict.fromkeys(preview_messages))
                elif step == 4 and not preview.valid:
                    step = 4
                if step != state.step:
                    state = record_imports.navigate(
                        actor.id, login_session.id, dataset.key, state.token, step
                    )
        if assistance:
            # Full versions of the same bounded display examples, for accessible disclosure.
            for index in range(len(source.headers)):
                samples = (
                    row.values[index]
                    for row in source.rows[:PROFILE_SCAN_ROWS]
                    if index not in row.formulas and row.values[index].strip()
                )
                mapping_samples.append(distinct_samples(samples))
        if assistance:
            format_columns = list(assistance.format_columns)
        format_columns = show_source_format_issues(format_columns, source_format_issues)
        for issue in source_format_issues:
            message = mapping_issue_message(issue, mode)
            if not message:
                continue
            step_mapping_errors.append(message)
            if issue.column_index is not None:
                mapping_errors[issue.column_index] = message
        if step_mapping_errors:
            error = merge_error_messages(error, *step_mapping_errors)
        success = state is not None and state.imported_count is not None
        automatic = source is not None and (source.kind == "csv" or len(source.worksheets) == 1)
        completed = {
            1: state is not None,
            2: bool(source and source.headers),
            3: bool(state and state.mapping_ready),
            4: bool(state and state.validation_valid),
        }
        labels = ("Upload file", "Select worksheet", "Map columns", "Preview & import")
        steps = []
        for number, label in enumerate(labels, 1):
            done = success or completed[number]
            reachable = bool(
                state
                and not success
                and number != step
                and (done or number == 3 and source and source.headers)
                and not (number == 2 and automatic)
            )
            steps.append(dict(number=number, label=label, done=done, reachable=reachable))
        preview_fields = [field for field in fields if preview and field.key in preview.mapping]
        preview_rows = preview.sample if preview and preview.valid else []
        report_rows = []
        if preview and preview.errors:
            rows = {row.number: row for row in preview.source.rows}
            for number, errors in preview.row_error_groups:
                row = rows[number]
                identifier_values = [
                    row.values[preview.mapping.index(key)]
                    for key in required_keys
                    if key in identifier_keys and key in preview.mapping
                ]
                details = []
                for item in errors:
                    value = None
                    if item.header in preview.source.headers:
                        index = preview.source.headers.index(item.header)
                        value = "[Formula]" if index in row.formulas else row.values[index]
                    details.append(dict(field=item.field, value=value, message=item.message))
                report_rows.append(
                    dict(number=number, identifier=" · ".join(identifier_values), errors=details)
                )
        back_step = 3 if step == 4 else 1 if automatic else step - 1
        return render_authenticated_template(
            "pages/datasets/import/wizard.html",
            mode=mode,
            difference_page=parse_page_parameter(),
            difference_search=request.args.get("q", ""),
            difference_statuses=request.args.getlist("status"),
            dataset=dataset,
            state=state,
            step=step,
            steps=steps,
            source=source,
            assistance=assistance,
            preview=preview,
            fields=fields,
            mapping_samples=mapping_samples,
            format_columns=format_columns,
            mapping_errors=mapping_errors,
            required_keys=required_keys,
            identifier_keys=identifier_keys,
            preview_fields=preview_fields,
            preview_rows=preview_rows,
            report_rows=report_rows,
            success=success,
            automatic=automatic,
            back_step=back_step,
            error=error,
            current_csrf=login_session.csrf_token,
            mapping_reason_text=(
                "This import replaces all records in the draft, so every field must be mapped."
            ),
        ), status

    def import_redirect(key, state):
        return redirect(
            url_for("dataset_import_step", key=key, token=state.token, step=state.step), code=303
        )

    @import_route(
        "/datasets/<key>/concept/records/import-new-version",
        capability="can_maintain_dataset",
        methods=["GET", "POST"],
    )
    @import_route(
        "/datasets/<key>/records/import", capability="can_maintain_dataset", methods=["GET", "POST"]
    )
    @upload_limit_route
    @concept_route
    def import_dataset_records(key: str):
        mode = "new_version"
        actor, login_session = require_authenticated()
        require_dataset_dependencies()
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        reason = record_imports.eligibility(actor.id, key)
        if reason:
            raise ImportValidationError(". ".join(reason))
        state = record_imports.active(actor.id, login_session.id, key)
        if state and state.mode != mode:
            raise ImportValidationError(
                "Cancel the older staged import, then upload the complete file again."
            )
        if request.method == "GET":
            if state:
                state = record_imports.navigate(
                    actor.id,
                    login_session.id,
                    key,
                    state.token,
                    4 if state.mapping_ready else state.step,
                )
                return import_redirect(key, state)
            return render_import_wizard(dataset, login_session)
        require_csrf(login_session.csrf_token)
        if (
            set(request.form) != {"csrf_token"}
            or set(request.files) != {"file"}
            or len(request.files.getlist("file")) != 1
        ):
            return render_import_wizard(
                dataset, login_session, state, error="Submit exactly one import file", status=400
            )
        uploaded = request.files["file"]
        try:
            state, _source, _assistance = record_imports.begin(
                actor.id,
                login_session.id,
                key,
                uploaded.filename or "",
                uploaded.stream.read(MAX_UPLOAD_BYTES + 1),
                mode=mode,
            )
        except ImportValidationError as error:
            return render_import_wizard(dataset, login_session, state, error=str(error), status=400)
        return import_redirect(key, state)

    @import_route(
        "/datasets/<key>/records/import/<token>/step/<int:step>",
        capability="can_maintain_dataset",
        methods=["GET"],
    )
    @bounded_import_route
    @concept_route
    def dataset_import_step(key: str, token: str, step: int):
        actor, login_session = require_authenticated()
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        state = record_imports.navigate(actor.id, login_session.id, key, token, step)
        return render_import_wizard(dataset, login_session, state, step=step)

    @import_route(
        "/datasets/<key>/records/import/<token>/worksheet",
        capability="can_maintain_dataset",
        methods=["POST"],
    )
    @bounded_import_route
    @concept_route
    def select_import_worksheet(key: str, token: str):
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        expected = {"csrf_token", "worksheet"}
        going_back = "go_step" in request.form
        if going_back:
            expected.add("go_step")
        if (
            set(request.form) != expected
            or any(len(request.form.getlist(name)) != 1 for name in expected)
            or going_back
            and request.form["go_step"] != "1"
        ):
            abort(400)
        if going_back and not request.form["worksheet"]:
            state = record_imports.navigate(actor.id, login_session.id, key, token, 1)
            return import_redirect(key, state)
        try:
            state, _source, _assistance = record_imports.select_worksheet(
                actor.id, login_session.id, key, token, request.form["worksheet"]
            )
        except ImportValidationError as error:
            dataset = dataset_records.get_dataset_for_add(actor.id, key)
            state = record_imports.state(actor.id, login_session.id, key, token)
            return render_import_wizard(
                dataset, login_session, state, step=2, error=str(error), status=400
            )
        if going_back:
            state = record_imports.navigate(actor.id, login_session.id, key, token, 1)
        return import_redirect(key, state)

    def submitted_source_format_components(mapping):
        return tuple(
            {
                "source": request.form.get(f"source_{index}", ""),
                **{
                    part: request.form[f"{part}_{index}"]
                    for part in ("date", "time")
                    if f"{part}_{index}" in request.form
                },
                "timezone": request.form.get(f"timezone_{index}", ""),
                "fold": request.form.get(f"fold_{index}", ""),
            }
            for index, _target in enumerate(mapping)
        )

    def show_source_format_issues(columns, issues):
        columns = list(columns)
        for issue in issues:
            if issue.column_index is None or issue.column_index >= len(columns):
                continue
            column = columns[issue.column_index]
            if column is not None:
                message = mapping_issue_message(issue)
                columns[issue.column_index] = {
                    **column,
                    "state": "failing",
                    "verdict": message,
                    "error": message,
                }
        return columns

    def submitted_component_names(count):
        # Components are optional only for the unchanged canonical-identifier contract.
        return {f"{part}_{index}" for part in ("date", "time") for index in range(count)} & set(
            request.form
        )

    @import_route(
        "/datasets/<key>/records/import/<token>/formats",
        capability="can_maintain_dataset",
        methods=["POST"],
    )
    @bounded_import_route
    @concept_route
    def review_source_formats(key: str, token: str):
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        state = record_imports.state(actor.id, login_session.id, key, token)
        if state.mapping is None:
            raise NotFoundError("Import not found")
        column_count = len(state.mapping)
        expected = {
            f"{prefix}_{index}"
            for prefix in ("mapping", "source", "timezone", "fold")
            for index in range(column_count)
        } | {"csrf_token"}
        expected |= submitted_component_names(column_count)
        if set(request.form) != expected or any(
            len(request.form.getlist(name)) != 1 for name in expected
        ):
            abort(400)
        mapping = tuple(
            None
            if request.form[f"mapping_{index}"] == "__ignored__"
            else request.form[f"mapping_{index}"]
            for index in range(column_count)
        )
        _state, _source, assistance, issues = record_imports.submitted_source_format_assistance(
            actor.id,
            login_session.id,
            key,
            token,
            mapping,
            submitted_source_format_components(mapping),
        )
        columns = show_source_format_issues(assistance.format_columns, issues)
        return render_template(
            "pages/datasets/import/source_format_fragments.html", format_columns=columns
        )

    @import_route(
        "/datasets/<key>/records/import/<token>/mapping",
        capability="can_maintain_dataset",
        methods=["GET"],
    )
    @bounded_import_route
    @concept_route
    def review_import_mapping(key: str, token: str):
        return dataset_import_step(key, token, 3)

    @import_route(
        "/datasets/<key>/records/import/<token>/preview",
        capability="can_maintain_dataset",
        methods=["GET", "POST"],
    )
    @bounded_import_route
    @concept_route
    def preview_record_import(key: str, token: str):
        actor, login_session = require_authenticated()
        state = record_imports.state(actor.id, login_session.id, key, token)
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        if state.mapping is None:
            raise NotFoundError("Import not found")
        column_count = len(state.mapping)
        if request.method == "GET":
            return dataset_import_step(key, token, 4)
        require_csrf(login_session.csrf_token)
        expected = {f"mapping_{index}" for index in range(column_count)} | {"csrf_token"}
        format_names = {
            f"{prefix}_{index}"
            for prefix in ("source", "timezone", "fold")
            for index in range(column_count)
        }
        if set(request.form) & format_names:
            expected |= format_names
            expected |= submitted_component_names(column_count)
        target_step = request.form.get("go_step")
        if target_step is not None:
            if len(request.form.getlist("go_step")) != 1 or target_step not in {"1", "2", "4"}:
                abort(400)
            # Check the original state before accepting a navigation request.
            record_imports.navigate(actor.id, login_session.id, key, token, int(target_step))
            expected.add("go_step")
        if set(request.form) != expected or any(
            len(request.form.getlist(name)) != 1 for name in expected
        ):
            return render_import_wizard(
                dataset,
                login_session,
                state,
                step=3,
                error="Submit exactly one mapping for every source column",
                status=400,
            )
        mapping = tuple(
            None
            if request.form[f"mapping_{index}"] == "__ignored__"
            else request.form[f"mapping_{index}"]
            for index in range(column_count)
        )
        format_components = (
            submitted_source_format_components(mapping)
            if set(request.form) & format_names
            else None
        )
        try:
            preview = record_imports.preview(
                actor.id,
                login_session.id,
                key,
                token,
                mapping,
                format_components=format_components,
            )
        except SourceFormatComponentsError as error:
            return render_import_wizard(
                dataset,
                login_session,
                state,
                step=3,
                status=400,
                selected_mapping=mapping,
                source_format_issues=error.issues,
            )
        except NotFoundError:
            raise
        except Exception:
            logging.getLogger(__name__).exception("Import mapping validation failed")
            state = record_imports.state(actor.id, login_session.id, key, token)
            return render_import_wizard(
                dataset,
                login_session,
                state,
                step=3,
                error=(
                    "Validation could not be completed. No records have been imported. Try again."
                ),
                status=500,
            )
        state = record_imports.state(actor.id, login_session.id, key, token)
        if target_step in {"1", "2"}:
            state = record_imports.navigate(
                actor.id, login_session.id, key, token, int(target_step)
            )
            return import_redirect(key, state)
        if preview.non_row_errors:
            return render_import_wizard(
                dataset,
                login_session,
                state,
                step=3,
                error=merge_error_messages(
                    *(
                        mapping_issue_message(item, state.mode) or item.message
                        for item in preview.non_row_errors
                    )
                ),
                status=400,
            )
        return import_redirect(key, state)

    @import_route(
        "/datasets/<key>/records/import/<token>/confirm",
        capability="can_maintain_dataset",
        methods=["POST"],
    )
    @concept_route
    def confirm_record_import(key: str, token: str):
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        state = record_imports.state(actor.id, login_session.id, key, token)
        expected = {"csrf_token", "confirmation"} if state.mode == "new_version" else {"csrf_token"}
        if "acknowledge_deletions" in request.form and state.mode == "new_version":
            expected.add("acknowledge_deletions")
        if set(request.form) != expected or any(
            len(request.form.getlist(k)) != 1 for k in expected
        ):
            abort(400)
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        try:
            record_imports.confirm(
                actor.id,
                login_session.id,
                key,
                token,
                require_confirmation=True,
                confirmation=request.form.get("confirmation"),
                acknowledge_deletions=request.form.get("acknowledge_deletions") == "yes",
            )
        except NotFoundError:
            raise
        except (ImportValidationError, RecordConflictError):
            state = record_imports.state(actor.id, login_session.id, key, token)
            return render_import_wizard(
                dataset,
                login_session,
                state,
                step=4,
                error=(
                    "The import changed since validation. No records have been imported. "
                    "Review the validation results before continuing."
                ),
                status=409,
            )
        except Exception:
            logging.getLogger(__name__).exception("Import preview confirmation failed")
            state = record_imports.state(actor.id, login_session.id, key, token)
            return render_import_wizard(
                dataset,
                login_session,
                state,
                step=4,
                error=(
                    "The import could not be completed. No records have been imported. Try again."
                ),
                status=500,
            )
        if state.mode == "new_version":
            return redirect(
                url_for("dataset_records_view", key=key, concept=True, import_results=1), code=303
            )
        return import_redirect(key, record_imports.state(actor.id, login_session.id, key, token))

    @import_route(
        "/datasets/<key>/records/import/<token>/cancel",
        capability="can_maintain_dataset",
        methods=["POST"],
    )
    @concept_route
    def cancel_record_import(key: str, token: str):
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        if set(request.form) != {"csrf_token"} or len(request.form.getlist("csrf_token")) != 1:
            abort(400)
        record_imports.cancel(actor.id, login_session.id, key, token)
        return redirect(url_for("dataset_records_view", key=key, concept=True), code=303)

    @import_route(
        "/datasets/<key>/concept/hierarchy-import",
        capability="can_maintain_dataset",
        methods=["GET", "POST"],
    )
    def import_hierarchy_file(key: str):
        if hierarchy_imports is None:
            abort(404)
        actor, login_session = require_authenticated()
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        if dataset.kind != "hierarchy":
            abort(404)
        state = hierarchy_imports.active(actor.id, login_session.id, key)
        if request.method == "GET":
            preview = (
                hierarchy_imports.preview(actor.id, login_session.id, key, state.token)
                if state
                else None
            )
            return render_authenticated_template(
                "pages/datasets/hierarchy_import.html",
                dataset=dataset,
                state=state,
                preview=preview,
                current_csrf=login_session.csrf_token,
            )
        require_csrf(login_session.csrf_token)
        if (
            set(request.form) != {"csrf_token"}
            or set(request.files) != {"file"}
            or len(request.files.getlist("file")) != 1
        ):
            return render_authenticated_template(
                "pages/datasets/hierarchy_import.html",
                dataset=dataset,
                state=state,
                preview=None,
                current_csrf=login_session.csrf_token,
                error="Submit exactly one Excel workbook",
            ), 400
        uploaded = request.files["file"]
        try:
            state = hierarchy_imports.begin(
                actor.id,
                login_session.id,
                key,
                uploaded.filename or "",
                uploaded.stream.read(MAX_UPLOAD_BYTES + 1),
            )
        except ImportValidationError as error:
            return render_authenticated_template(
                "pages/datasets/hierarchy_import.html",
                dataset=dataset,
                state=state,
                preview=None,
                current_csrf=login_session.csrf_token,
                error=str(error),
            ), 400
        return redirect(url_for("hierarchy_import_preview", key=key, token=state.token), code=303)

    @import_route(
        "/datasets/<key>/concept/hierarchy-import/<token>",
        capability="can_maintain_dataset",
        methods=["GET"],
    )
    def hierarchy_import_preview(key: str, token: str):
        if hierarchy_imports is None:
            abort(404)
        actor, login_session = require_authenticated()
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        state = hierarchy_imports.state(actor.id, login_session.id, key, token)
        preview = hierarchy_imports.preview(actor.id, login_session.id, key, token)
        return render_authenticated_template(
            "pages/datasets/hierarchy_import.html",
            dataset=dataset,
            state=state,
            preview=preview,
            current_csrf=login_session.csrf_token,
        )

    @import_route(
        "/datasets/<key>/concept/hierarchy-import/<token>/confirm",
        capability="can_maintain_dataset",
        methods=["POST"],
    )
    def confirm_hierarchy_import(key: str, token: str):
        if hierarchy_imports is None:
            abort(404)
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        expected = {"csrf_token", "confirmation"}
        if "acknowledge_removals" in request.form:
            expected.add("acknowledge_removals")
        if set(request.form) != expected or any(
            len(request.form.getlist(name)) != 1 for name in expected
        ):
            abort(400)
        try:
            hierarchy_imports.confirm(
                actor.id,
                login_session.id,
                key,
                token,
                confirmation=request.form["confirmation"],
                acknowledge_removals=request.form.get("acknowledge_removals") == "yes",
            )
        except (ImportValidationError, StaleRecordError, ConflictError) as error:
            dataset = dataset_records.get_dataset_for_add(actor.id, key)
            state = hierarchy_imports.state(actor.id, login_session.id, key, token)
            preview = hierarchy_imports.preview(actor.id, login_session.id, key, token)
            return render_authenticated_template(
                "pages/datasets/hierarchy_import.html",
                dataset=dataset,
                state=state,
                preview=preview,
                current_csrf=login_session.csrf_token,
                error=str(error),
            ), 409
        return redirect(url_for("dataset_records_view", key=key, concept=True), code=303)

    @import_route(
        "/datasets/<key>/concept/hierarchy-import/<token>/cancel",
        capability="can_maintain_dataset",
        methods=["POST"],
    )
    def cancel_hierarchy_import(key: str, token: str):
        if hierarchy_imports is None:
            abort(404)
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        if set(request.form) != {"csrf_token"} or len(request.form.getlist("csrf_token")) != 1:
            abort(400)
        hierarchy_imports.cancel(actor.id, login_session.id, key, token)
        return redirect(url_for("dataset_records_view", key=key, concept=True), code=303)

    def render_record_form(
        dataset,
        fields,
        *,
        csrf,
        submitted=None,
        errors=None,
        status=200,
        record=None,
    ):
        submitted = submitted or {}
        errors = errors or {}
        record_type = (
            dataset.key[:-3] + "y" if dataset.key.endswith("ies") else dataset.key.removesuffix("s")
        )
        response = render_authenticated_template(
            "pages/datasets/records/form.html",
            dataset=dataset,
            fields=fields,
            current_csrf=csrf,
            submitted=submitted,
            errors=errors,
            record=record,
            record_type=record_type,
        )
        return (response, status) if status != 200 else response

    @dataset_route(
        "/datasets/<key>/records/new", capability="can_maintain_dataset", methods=["GET"]
    )
    @concept_route
    def new_dataset_record(key: str):
        actor, login_session = require_authenticated()
        _authorization, dataset_service, field_service, _business_keys, record_service = (
            require_dataset_dependencies()
        )
        try:
            dataset = record_service.get_dataset_for_add(actor.id, key)
            fields = field_service.list_fields(actor.id, key)
        except NotFoundError:
            abort(404)
        if dataset.kind == "mapping" and (
            dataset.mapping_mode == "linked" or mapping_incomplete_message(fields)
        ):
            abort(404)
        return render_record_form(dataset, fields, csrf=login_session.csrf_token)

    @dataset_route("/datasets/<key>/records", capability="can_maintain_dataset", methods=["POST"])
    @concept_route
    def create_dataset_record(key: str):
        actor, login_session = require_authenticated()
        _authorization, dataset_service, field_service, _business_keys, record_service = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        try:
            dataset = record_service.get_dataset_for_add(actor.id, key)
            fields = field_service.list_fields(actor.id, key)
        except NotFoundError:
            abort(404)
        expected = {field.key for field in fields}
        submitted: dict[str, str] = {}
        errors: dict[str, str] = {}
        for form_key, form_values in request.form.lists():
            if form_key == "csrf_token":
                continue
            if form_key not in expected:
                errors[form_key] = "Unknown Dataset field"
                errors["_record"] = "Record contains unknown Dataset fields"
            elif len(form_values) != 1:
                errors[form_key] = "Submit exactly one value"
            else:
                submitted[form_key] = form_values[0]
        if errors:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors=errors,
                status=400,
            )
        try:
            record_service.create_record(actor.id, key, submitted)
        except RecordValidationError as error:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors=error.errors,
                status=400,
            )
        except ValidationError as error:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors={"_record": str(error)},
                status=400,
            )
        except RecordConflictError as error:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors={error.field_key or "_record": str(error)},
                status=409,
            )
        except NotFoundError:
            abort(404)
        return redirect(url_for("dataset_records_view", key=key, concept=True))

    def record_form_values(record):
        return {
            value.field_key: get_datatype(value.data_type).text(value.value)
            for value in record.values
        }

    def parsed_record_form(fields, infrastructure):
        expected = {field.key for field in fields}
        submitted: dict[str, str] = {}
        errors: dict[str, str] = {}
        for form_key, form_values in request.form.lists():
            if form_key in infrastructure:
                continue
            if form_key not in expected:
                errors[form_key] = "Unknown Dataset field"
                errors["_record"] = "Record contains unknown Dataset fields"
            elif len(form_values) != 1:
                errors[form_key] = "Submit exactly one value"
            else:
                submitted[form_key] = form_values[0]
        return submitted, errors

    def exact_expected_revision():
        values = request.form.getlist("expected_revision")
        if len(values) != 1:
            raise ValidationError("Submit exactly one expected revision")
        return parse_expected_revision(values[0])

    @dataset_route(
        "/datasets/<key>/records/<record_id>/edit",
        capability="can_maintain_dataset",
        methods=["GET", "POST"],
    )
    @concept_route
    def edit_dataset_record(key: str, record_id: str):
        actor, login_session = require_authenticated()
        _authorization, _datasets, field_service, _business_keys, record_service = (
            require_dataset_dependencies()
        )
        try:
            dataset = record_service.get_dataset_for_add(actor.id, key)
            fields = field_service.list_fields(actor.id, key)
            record = record_service.get_record_for_edit(actor.id, key, record_id)
        except NotFoundError:
            abort(404)
        if request.method == "GET":
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=record_form_values(record),
                record=record,
            )
        require_csrf(login_session.csrf_token)
        submitted, errors = parsed_record_form(fields, {"csrf_token", "expected_revision"})
        try:
            expected_revision = exact_expected_revision()
        except ValidationError as error:
            errors["_record"] = str(error)
        if errors:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors=errors,
                status=400,
                record=record,
            )
        try:
            record_service.update_record(actor.id, key, record_id, expected_revision, submitted)
        except RecordValidationError as error:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors=error.errors,
                status=400,
                record=record,
            )
        except ValidationError as error:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors={"_record": str(error)},
                status=400,
                record=record,
            )
        except (RecordConflictError, StaleRecordError) as error:
            return render_record_form(
                dataset,
                fields,
                csrf=login_session.csrf_token,
                submitted=submitted,
                errors={"_record": str(error)},
                status=409,
                record=record,
            )
        except NotFoundError:
            abort(404)
        return redirect(url_for("dataset_records_view", key=key, concept=True))

    @dataset_route(
        "/datasets/<key>/concept/records/<record_id>/restore",
        capability="can_maintain_dataset",
        methods=["POST"],
    )
    @concept_route
    def restore_dataset_record(key: str, record_id: str):
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        expected = {"csrf_token", "expected_revision", "concept_id"}
        if set(request.form) != expected or any(
            len(request.form.getlist(k)) != 1 for k in expected
        ):
            abort(400)
        try:
            dataset_records.restore_record(
                actor.id,
                key,
                record_id,
                exact_expected_revision(),
                concept_id=request.form["concept_id"],
            )
        except (ValidationError, RecordConflictError, StaleRecordError) as error:
            return render_authenticated_template(
                "pages/datasets/records/restore_error.html", error=str(error), key=key
            ), 409
        except NotFoundError:
            abort(404)
        return redirect(url_for("dataset_records_view", key=key, concept=True), code=303)

    @dataset_route(
        "/datasets/<key>/records/<record_id>/delete",
        capability="can_maintain_dataset",
        methods=["GET", "POST"],
    )
    @concept_route
    def delete_dataset_record(key: str, record_id: str):
        actor, login_session = require_authenticated()
        _authorization, _datasets, _fields, business_keys, record_service = (
            require_dataset_dependencies()
        )
        try:
            dataset = record_service.get_dataset_for_add(actor.id, key)
            record = record_service.get_record_for_edit(actor.id, key, record_id)
            identifier_keys = business_keys.get_business_key(actor.id, key).field_keys
        except NotFoundError:
            abort(404)
        values = {value.field_key: value.value for value in record.values}
        incomplete = any(field_key not in values for field_key in identifier_keys)
        identifier = (
            "Incomplete record"
            if incomplete
            else " / ".join(str(values[field_key]) for field_key in identifier_keys)
        )
        error = None
        status = 200
        if request.method == "POST":
            require_csrf(login_session.csrf_token)
            try:
                if request.form.getlist("confirm") != ["yes"]:
                    raise ValidationError("Explicit confirmation is required")
                expected_revision = exact_expected_revision()
                extras = set(request.form) - {"csrf_token", "confirm", "expected_revision"}
                if extras:
                    raise ValidationError("Unknown form field")
                record_service.delete_record(actor.id, key, record_id, expected_revision)
            except ValidationError as exc:
                error, status = str(exc), 400
            except StaleRecordError as exc:
                error, status = str(exc), 409
            except NotFoundError:
                abort(404)
            else:
                return redirect(url_for("dataset_records_view", key=key, concept=True))
        response = render_authenticated_template(
            "pages/datasets/records/delete.html",
            dataset=dataset,
            record=record,
            identifier=identifier,
            current_csrf=login_session.csrf_token,
            error=error,
        )
        return (response, status) if status != 200 else response

    @dataset_route("/datasets/<key>/edit", capability="can_maintain_dataset", methods=["GET"])
    def edit_dataset(key: str):
        actor, login_session = require_authenticated()
        _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
            require_dataset_dependencies()
        )
        try:
            dataset = dataset_service.get_dataset_for_edit(actor.id, key)
        except NotFoundError:
            abort(404)
        return render_dataset_configuration(dataset.key, standalone=True, edit_mode=True)

    @dataset_route("/datasets/<key>", capability="can_maintain_dataset", methods=["POST"])
    def update_dataset(key: str):
        actor, login_session = require_authenticated()
        _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        name = request.form.get("name", "")
        description = request.form.get("description", "")
        technical_key = request.form.get("technical_key")
        try:
            owner_change = None
            if {"owner_team_id", "expected_owner_team_id"} & set(request.form):
                if any(
                    len(request.form.getlist(field)) != 1
                    for field in ("owner_team_id", "expected_owner_team_id")
                ):
                    raise ValidationError(
                        "Submit exactly one expected and new owning team. Reopen the form."
                    )
                owner_change = (
                    request.form["expected_owner_team_id"],
                    request.form["owner_team_id"],
                )
            dataset = dataset_service.update_dataset(
                actor.id,
                key,
                name,
                description,
                owner_change=owner_change,
                icon=request.form.get("icon"),
                new_key=technical_key,
            )
        except NotFoundError:
            abort(404)
        except (ValidationError, ConflictError) as error:
            try:
                dataset = dataset_service.get_dataset_for_edit(actor.id, key)
            except NotFoundError:
                abort(404)
            message = str(error)
            if message.startswith("Dataset name"):
                error_field = "name"
            elif message.startswith("Dataset key"):
                error_field = "technical_key"
            elif message.startswith("Dataset description"):
                error_field = "description"
            else:
                error_field = None
            return (
                render_dataset_configuration(
                    dataset.key,
                    standalone=True,
                    edit_mode=True,
                    name=name,
                    description=description,
                    error=message,
                    error_field=error_field,
                ),
                409 if isinstance(error, ConflictError) else 400,
            )
        return redirect(url_for("dataset_configuration", key=dataset.key))

    @dataset_route(
        "/datasets/id/<dataset_id>/delete",
        capability="can_maintain_dataset",
        methods=["GET", "POST"],
    )
    def dataset_delete(dataset_id: str):
        actor, login_session = require_authenticated()
        _auth, service, *_rest = require_dataset_dependencies()
        error = None
        status = 200
        try:
            if request.method == "POST":
                require_csrf(login_session.csrf_token)
                # A duplicate/missing field cannot select a favorable value.
                name = request.form.getlist("confirmation_name")
                expected = request.form.getlist("expected_updated_at")
                service.delete_dataset(
                    actor.id,
                    dataset_id,
                    name[0] if len(name) == 1 else None,
                    expected[0] if len(expected) == 1 else None,
                )
                return redirect(url_for("dataset_list", deleted="1"), code=303)
        except NotFoundError:
            abort(404)
        except (ValidationError, ConflictError) as failure:
            error = str(failure)
            status = 409 if isinstance(failure, ConflictError) else 400
        except PersistenceConflictError:
            error = (
                "Dataset deletion could not be completed. Reopen the confirmation and try again."
            )
            status = 409
        try:
            context = service.get_dataset_for_deletion(actor.id, dataset_id)
        except NotFoundError:
            abort(404)
        except PersistenceConflictError:
            abort(503, description="Dataset confirmation is temporarily unavailable.")
        return render_authenticated_template(
            "pages/datasets/delete.html",
            deletion=context,
            error=error,
        ), status

    def require_dataset_schema_admin(actor_id: str, key: str):
        (
            authorization_service,
            dataset_service,
            _field_service,
            _business_key_service,
            _record_service,
        ) = require_dataset_dependencies()
        try:
            dataset = dataset_service.get_dataset(actor_id, key)
        except NotFoundError:
            abort(404)
        if not authorization_service.has_capability(actor_id, "can_maintain_dataset", dataset.id):
            audit_security("security.authorization_denied", actor_user_id=actor_id)
            abort(404)
        return dataset

    def render_dataset_field_create_form(
        dataset,
        *,
        csrf: str,
        key: str = "",
        name: str = "",
        data_type: str = "string",
        is_required: bool = False,
        is_unique: bool = False,
        is_business_key_component: bool = False,
        error=None,
    ):
        actor, _login_session = require_authenticated()
        _authorization, dataset_service, field_service, *_rest = require_dataset_dependencies()
        mapping_fields = (
            field_service.list_fields(actor.id, dataset.key) if dataset.kind == "mapping" else ()
        )
        has_mapping_source_identifier = any(
            field.mapping_role == "source_identifier" for field in mapping_fields
        )
        mapping_source_fields = ()
        if dataset.kind == "mapping" and dataset.mapping_mode == "linked":
            try:
                visible_dataset_lookup = dataset_service.get_visible_dataset_by_id
                source = visible_dataset_lookup(actor.id, dataset.source_dataset_id)
            except NotFoundError:
                source = None
            if source is not None:
                existing = {
                    field.source_field_key
                    for field in mapping_fields
                    if getattr(field, "source_field_key", None)
                }
                mapping_source_fields = tuple(
                    field
                    for field in field_service.list_fields(actor.id, source.key)
                    if field.key not in existing
                )
        default_mapping_role = (
            "mapping_target"
            if dataset.mapping_mode == "linked" or has_mapping_source_identifier
            else "source_identifier"
        )
        selected_mapping_role = request.form.get("mapping_field_role", default_mapping_role)
        if has_mapping_source_identifier and selected_mapping_role == "source_identifier":
            selected_mapping_role = "mapping_target"
        return render_authenticated_template(
            "pages/datasets/fields/create.html",
            definition=request.form.get("definition", ""),
            unit=request.form.get("unit", ""),
            dataset=dataset,
            key=key,
            name=name,
            data_type=data_type,
            is_required=is_required,
            is_unique=is_unique,
            is_business_key_component=is_business_key_component,
            error=error,
            current_csrf=csrf,
            type_choices=tuple(DATATYPES.values()),
            mapping_field_role=selected_mapping_role,
            has_mapping_source_identifier=has_mapping_source_identifier,
            mapping_source_field_key=request.form.get("source_field_key", ""),
            mapping_source_fields=mapping_source_fields,
            hierarchy_scope=request.form.get("hierarchy_scope", "leaves"),
        )

    @dataset_route("/datasets/<key>/fields/new", capability="can_maintain_dataset", methods=["GET"])
    @concept_route
    def new_dataset_field(key: str):
        actor, login_session = require_authenticated()
        dataset = require_dataset_schema_admin(actor.id, key)
        return render_dataset_field_create_form(dataset, csrf=login_session.csrf_token)

    @dataset_route("/datasets/<key>/fields", capability="can_maintain_dataset", methods=["POST"])
    @concept_route
    def create_dataset_field(key: str):
        actor, login_session = require_authenticated()
        _authorization, _dataset_service, field_service, _business_key_service, _record_service = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        field_key = request.form.get("key", "")
        name = request.form.get("name", "")
        data_type = request.form.get("data_type", "")
        is_required = "is_required" in request.form
        is_unique = "is_unique" in request.form
        is_business_key_component = "is_business_key_component" in request.form
        try:
            field_service.create_field(
                actor.id,
                key,
                field_key,
                name,
                data_type,
                is_required,
                is_unique,
                is_business_key_component,
                definition=request.form.get("definition", ""),
                unit=request.form.get("unit", ""),
                mapping_role=request.form.get("mapping_field_role") or None,
                source_field_key=request.form.get("source_field_key") or None,
                hierarchy_scope=request.form.get("hierarchy_scope") or None,
            )
        except NotFoundError:
            abort(404)
        except (ValidationError, ConflictError) as error:
            dataset = require_dataset_schema_admin(actor.id, key)
            response = render_dataset_field_create_form(
                dataset,
                csrf=login_session.csrf_token,
                key=field_key,
                name=name,
                data_type=data_type,
                is_required=is_required,
                is_unique=is_unique,
                is_business_key_component=is_business_key_component,
                error=str(error),
            )
            return response, 409 if isinstance(error, ConflictError) else 400
        return redirect(
            url_for("dataset_detail", key=key, concept=True, tab="fields", _anchor="fields")
        )

    def render_dataset_field_edit_form(
        dataset,
        field,
        *,
        csrf: str,
        name: str,
        data_type: str,
        is_required: bool,
        is_unique: bool,
        is_business_key_component: bool,
        key_value: str,
        may_edit_key: bool,
        error=None,
    ):
        return render_authenticated_template(
            "pages/datasets/fields/edit.html",
            definition=request.form.get("definition", field.definition),
            unit=request.form.get("unit", field.unit),
            dataset=dataset,
            field=field,
            name=name,
            data_type=data_type,
            is_required=is_required,
            is_unique=is_unique,
            is_business_key_component=is_business_key_component,
            key_value=key_value,
            may_edit_key=may_edit_key,
            error=error,
            current_csrf=csrf,
            type_choices=tuple(DATATYPES.values()),
            hierarchy_scope=request.form.get("hierarchy_scope", field.hierarchy_scope),
        )

    @dataset_route(
        "/datasets/<key>/fields/<field_key>/edit",
        capability="can_maintain_dataset",
        methods=["GET"],
    )
    @concept_route
    def edit_dataset_field(key: str, field_key: str):
        actor, login_session = require_authenticated()
        dataset = require_dataset_schema_admin(actor.id, key)
        _authorization, _dataset_service, field_service, business_key_service, record_service = (
            require_dataset_dependencies()
        )
        try:
            field = field_service.get_field(actor.id, key, field_key)
            business_key = business_key_service.get_business_key(actor.id, key)
            may_edit_key = datasets.get_dataset_configuration(actor.id, key).may_edit_key
        except NotFoundError:
            abort(404)
        return render_dataset_field_edit_form(
            dataset,
            field,
            csrf=login_session.csrf_token,
            name=field.name,
            data_type=field.data_type,
            is_required=field.is_required,
            is_unique=field.is_unique,
            is_business_key_component=field.key in business_key.field_keys,
            key_value=field.key,
            may_edit_key=may_edit_key,
        )

    @dataset_route(
        "/datasets/<key>/fields/<field_key>", capability="can_maintain_dataset", methods=["POST"]
    )
    @concept_route
    def update_dataset_field(key: str, field_key: str):
        actor, login_session = require_authenticated()
        _authorization, _dataset_service, field_service, _business_key_service, _record_service = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        name = request.form.get("name", "")
        data_type = request.form.get("data_type", "")
        is_required = "is_required" in request.form
        is_unique = "is_unique" in request.form
        is_business_key_component = "is_business_key_component" in request.form
        try:
            field_service.update_field_and_business_key_membership(
                actor.id,
                key,
                field_key,
                name,
                data_type,
                is_required,
                is_unique,
                is_business_key_component,
                new_key=request.form.get("key", field_key),
                definition=request.form.get("definition", ""),
                unit=request.form.get("unit", ""),
                hierarchy_scope=request.form.get("hierarchy_scope") or None,
            )
        except NotFoundError:
            abort(404)
        except (ValidationError, ConflictError) as error:
            dataset = require_dataset_schema_admin(actor.id, key)
            try:
                field = field_service.get_field(actor.id, key, field_key)
                may_edit_key = datasets.get_dataset_configuration(actor.id, key).may_edit_key
            except NotFoundError:
                abort(404)
            return (
                render_dataset_field_edit_form(
                    dataset,
                    field,
                    csrf=login_session.csrf_token,
                    name=name,
                    data_type=data_type,
                    is_required=is_required,
                    is_unique=is_unique,
                    is_business_key_component=is_business_key_component,
                    key_value=request.form.get("key", field.key),
                    may_edit_key=may_edit_key,
                    error=str(error),
                ),
                409 if isinstance(error, ConflictError) else 400,
            )
        return redirect(
            url_for("dataset_detail", key=key, concept=True, tab="fields", _anchor="fields")
        )

    @dataset_route(
        "/datasets/<key>/fields/<field_key>/delete",
        capability="can_maintain_dataset",
        methods=["POST"],
    )
    @concept_route
    def delete_dataset_field(key: str, field_key: str):
        actor, login_session = require_authenticated()
        _authorization, _dataset_service, field_service, business_key_service, record_service = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        try:
            field_service.delete_field(actor.id, key, field_key)
        except NotFoundError:
            abort(404)
        except ValidationError as error:
            dataset = require_dataset_schema_admin(actor.id, key)
            try:
                field = field_service.get_field(actor.id, key, field_key)
                business_key = business_key_service.get_business_key(actor.id, key)
                may_edit_key = datasets.get_dataset_configuration(actor.id, key).may_edit_key
            except NotFoundError:
                abort(404)
            return (
                render_dataset_field_edit_form(
                    dataset,
                    field,
                    csrf=login_session.csrf_token,
                    name=field.name,
                    data_type=field.data_type,
                    is_required=field.is_required,
                    is_unique=field.is_unique,
                    is_business_key_component=field.key in business_key.field_keys,
                    key_value=field.key,
                    may_edit_key=may_edit_key,
                    error=str(error),
                ),
                400,
            )
        return redirect(
            url_for("dataset_detail", key=key, concept=True, tab="fields", _anchor="fields")
        )

    @app.route("/setup", methods=["GET", "POST"])
    @public_route
    @anonymous_csrf_route
    def setup():
        settings = require_cookie_settings()
        if users.has_users():
            return redirect(url_for("login"))
        if request.method == "POST":
            require_csrf(request.cookies.get(settings.anonymous_csrf_name))
            try:
                users.create_initial_admin(
                    request.form["email"],
                    request.form["display_name"],
                    request.form["password"],
                )
            except UserManagementError as error:
                message = str(error)
                if message.startswith("Display name"):
                    error_field = "display_name"
                elif message.startswith("A valid email"):
                    error_field = "email"
                elif message.startswith("Password"):
                    error_field = "password"
                else:
                    error_field = None
                return (
                    render_anonymous_template(
                        "pages/auth/setup.html",
                        csrf_token=request.cookies.get(settings.anonymous_csrf_name),
                        error=message,
                        error_field=error_field,
                        email=request.form["email"],
                        display_name=request.form["display_name"],
                    ),
                    400,
                )
            response = redirect(url_for("login"))
            response.delete_cookie(
                settings.anonymous_csrf_name,
                **settings.common_options,
            )
            return response
        return anonymous_csrf_form_response(
            "pages/auth/setup.html",
            error_field=None,
            email=None,
            display_name=None,
        )

    @app.route("/login", methods=["GET", "POST"])
    @public_route
    @anonymous_csrf_route
    def login():
        settings = require_cookie_settings()
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
                render_anonymous_template(
                    "pages/auth/login.html",
                    csrf_token=request.cookies.get(settings.anonymous_csrf_name),
                    error=GENERIC_LOGIN_FAILURE,
                    next_target=return_target,
                    field_errors={},
                    email=request.form.get("email", "")[:MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS],
                ),
                401,
            )

        if request.method == "POST":
            require_csrf(request.cookies.get(settings.anonymous_csrf_name))
            submitted_email = request.form.get("email", "")
            submitted_password = request.form.get("password", "")
            field_errors = {}
            if not submitted_email.strip():
                field_errors["email"] = "Enter your email address."
            if not submitted_password:
                field_errors["password"] = "Enter your password."
            oversized_identifier = len(submitted_email) > MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS
            account_key = canonical_account_key(submitted_email)
            source_key = source_resolver.resolve(
                request.remote_addr,
                request.headers.get("X-Forwarded-For"),
            )
            attempt = login_limiter.reserve(source_key, account_key)
            if attempt is None:
                audit_security("security.login_failed")
                return invalid_credentials_response()
            if oversized_identifier:
                attempt.failed()
                audit_security("security.login_failed")
                return invalid_credentials_response()
            if field_errors:
                attempt.failed()
                return render_anonymous_template(
                    "pages/auth/login.html",
                    csrf_token=request.cookies.get(settings.anonymous_csrf_name),
                    error="Check the required fields below.",
                    field_errors=field_errors,
                    email=submitted_email[:MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS],
                    next_target=return_target,
                ), 400
            permit = authentication_admission.try_acquire()
            if permit is None:
                attempt.cancel()
                audit_security("security.login_failed")
                return invalid_credentials_response()
            try:
                with permit:
                    principal = authentication.authenticate(submitted_email, submitted_password)
            except Exception:
                logging.getLogger(__name__).exception("Browser authentication failed")
                attempt.failed()
                raise
            if principal is not None:
                attempt.succeeded()
                existing_token = request.cookies.get(settings.session_name)
                if existing_token:
                    sessions.revoke(existing_token)
                token, _ = sessions.create(principal)
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
            audit_security("security.login_failed")
            return invalid_credentials_response()
        return anonymous_csrf_form_response(
            "pages/auth/login.html", next_target=return_target, field_errors={}, email=None
        )

    @app.post("/logout")
    @public_route
    def logout():
        settings = cookie_settings()
        token = request.cookies.get(settings.session_name) if settings else None
        if not token:
            return redirect(url_for("index"))
        resolved = sessions.resolve_with_session(token)
        if resolved is None:
            return redirect(url_for("index"))
        require_csrf(resolved[1].csrf_token)
        if record_imports is not None:
            record_imports.revoke_session(resolved[1].id)
        if hierarchy_imports is not None:
            hierarchy_imports.revoke_session(resolved[1].id)
        sessions.revoke(token)
        response = redirect(url_for("index"))
        response.delete_cookie(settings.session_name, **settings.common_options)
        return response

    @app.post("/settings/theme")
    @authenticated_route
    def theme_preference():
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        try:
            users.update_theme_preference(actor.id, request.form.get("theme_preference", ""))
        except ValidationError:
            abort(400)
        return redirect(url_for("appearance_settings"), code=303)

    @app.get("/settings/appearance")
    @authenticated_route
    def appearance_settings():
        actor, login_session = require_authenticated()
        return render_authenticated_template(
            "pages/settings/appearance.html",
            settings_title="Appearance",
            active_navigation="appearance",
            theme_preference=actor.theme_preference,
            current_csrf=login_session.csrf_token,
        )

    def render_user_list(*, form_error=None, form_target_id=None, form_data=None, status=200):
        actor, login_session = require_capability("can_manage_access")
        managed_users = users.list_users(actor.id)
        user_teams = {}
        for relationship in memberships.list_relationships(actor.id):
            user_teams.setdefault(relationship.user_id, []).append(relationship)
        form_data = form_data or {}
        return render_template(
            "pages/users/list.html",
            users=managed_users,
            user_teams=user_teams,
            open_user=form_target_id or request.args.get("open_user"),
            form_error=form_error,
            form_target_id=form_target_id,
            form_email=form_data.get("email", ""),
            form_display_name=form_data.get("display_name", ""),
            form_role=form_data.get("role", "user"),
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        ), status

    @app.get("/users")
    @capability_route("can_manage_access")
    def user_list():
        return render_user_list()

    @app.post("/users")
    @capability_route("can_manage_access")
    def create_user():
        actor, login_session = require_capability("can_manage_access")
        require_csrf(login_session.csrf_token)
        try:
            users.create_user(
                actor.id,
                request.form.get("email", ""),
                request.form.get("display_name", ""),
                request.form.get("password", ""),
                request.form.get("role", "user"),
            )
        except (ValidationError, ConflictError) as error:
            return render_user_list(
                form_error=str(error),
                form_data=request.form,
                status=409 if isinstance(error, ConflictError) else 400,
            )
        return redirect(url_for("user_list"))

    @app.post("/users/<target_id>/<action>")
    @capability_route("can_manage_access")
    def change_user_state(target_id: str, action: str):
        actor, login_session = require_capability("can_manage_access")
        require_csrf(login_session.csrf_token)
        operations = {
            "disable": users.disable_user,
            "enable": users.enable_user,
            "delete": users.delete_user,
        }
        operation = operations.get(action)
        if operation is None:
            abort(404)
        try:
            operation(actor.id, target_id)
        except NotFoundError:
            abort(404)
        return redirect(url_for("user_list"))

    @app.post("/users/<target_id>/email")
    @capability_route("can_manage_access")
    def change_user_email(target_id: str):
        actor, login_session = require_capability("can_manage_access")
        require_csrf(login_session.csrf_token)
        try:
            users.change_email(actor.id, target_id, request.form.get("email", ""))
        except NotFoundError:
            abort(404)
        except (ValidationError, ConflictError) as error:
            return render_user_list(
                form_error=str(error),
                form_target_id=target_id,
                form_data=request.form,
                status=409 if isinstance(error, ConflictError) else 400,
            )
        return redirect(url_for("user_list"))

    def render_team_list(
        *, form_error=None, membership_error=None, form_target_id=None, form_name="", status=200
    ):
        actor, login_session = require_authenticated()
        if actor.role != "admin":
            return render_template(
                "pages/teams/readonly.html",
                teams=teams.list_teams(actor.id),
                current_actor=actor,
                current_csrf=login_session.csrf_token,
                active_navigation=active_navigation(),
                dataset_ui_enabled=dataset_ui_enabled,
            ), status
        managed_teams = teams.list_teams(actor.id)
        team_users = {}
        for relationship in memberships.list_relationships(actor.id):
            team_users.setdefault(relationship.team_id, []).append(relationship)
        open_team = form_target_id or request.args.get("open_team")
        selected_team = next((team for team in managed_teams if team.id == open_team), None)
        selected_team_members = (
            memberships.list_team_members(actor.id, selected_team.id) if selected_team else []
        )
        selected_eligible_users = (
            memberships.list_eligible_users(actor.id, selected_team.id) if selected_team else []
        )
        return render_template(
            "pages/teams/list.html",
            teams=managed_teams,
            team_users=team_users,
            selected_team=selected_team,
            selected_team_members=selected_team_members,
            selected_eligible_users=selected_eligible_users,
            form_error=form_error,
            membership_error=membership_error,
            form_target_id=form_target_id,
            form_name=form_name,
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        ), status

    @app.get("/teams")
    @authenticated_route
    def team_list():
        return render_team_list()

    @app.post("/teams")
    @capability_route("can_manage_access")
    def create_team():
        actor, login_session = require_capability("can_manage_access")
        require_csrf(login_session.csrf_token)
        try:
            teams.create_team(actor.id, request.form.get("name", ""))
        except (ValidationError, ConflictError) as error:
            return render_team_list(
                form_error=str(error),
                form_name=request.form.get("name", ""),
                status=409 if isinstance(error, ConflictError) else 400,
            )
        return redirect(url_for("team_list"))

    @app.post("/teams/<team_id>/rename")
    @capability_route("can_manage_access")
    def rename_team(team_id: str):
        actor, login_session = require_capability("can_manage_access")
        require_csrf(login_session.csrf_token)
        try:
            teams.rename_team(actor.id, team_id, request.form.get("name", ""))
        except NotFoundError:
            abort(404)
        except (ValidationError, ConflictError) as error:
            return render_team_list(
                form_error=str(error),
                form_target_id=team_id,
                form_name=request.form.get("name", ""),
                status=409 if isinstance(error, ConflictError) else 400,
            )
        return redirect(url_for("team_list"))

    @app.post("/teams/<team_id>/delete")
    @capability_route("can_manage_access")
    def delete_team(team_id: str):
        actor, login_session = require_capability("can_manage_access")
        require_csrf(login_session.csrf_token)
        try:
            teams.delete_team(actor.id, team_id)
        except NotFoundError:
            abort(404)
        return redirect(url_for("team_list"))

    @app.get("/teams/<team_id>/members")
    @capability_route("can_manage_access")
    def team_members(team_id: str):
        actor, login_session = require_capability("can_manage_access")
        managed_members = memberships.list_team_members(actor.id, team_id)
        eligible_users = memberships.list_eligible_users(actor.id, team_id)
        return render_template(
            "pages/teams/members.html",
            members=managed_members,
            eligible_users=eligible_users,
            team_id=team_id,
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    @app.post("/teams/<team_id>/members")
    @capability_route("can_manage_access")
    def create_membership(team_id: str):
        actor, login_session = require_capability("can_manage_access")
        require_csrf(login_session.csrf_token)
        try:
            memberships.create_membership(actor.id, request.form["user_id"], team_id)
        except NotFoundError:
            abort(404)
        except (ValidationError, ConflictError) as error:
            return render_team_list(
                membership_error=str(error),
                form_target_id=team_id,
                status=409 if isinstance(error, ConflictError) else 400,
            )
        return redirect(url_for("team_list", open_team=team_id))

    @app.post("/teams/<team_id>/members/<membership_id>/delete")
    @capability_route("can_manage_access")
    def delete_membership(team_id: str, membership_id: str):
        actor, login_session = require_capability("can_manage_access")
        require_csrf(login_session.csrf_token)
        try:
            memberships.delete_membership(actor.id, team_id, membership_id)
        except NotFoundError:
            abort(404)
        except (ValidationError, ConflictError) as error:
            return render_team_list(
                membership_error=str(error),
                form_target_id=team_id,
                status=409 if isinstance(error, ConflictError) else 400,
            )
        return redirect(url_for("team_list", open_team=team_id))

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

    missing_capabilities = [
        rule.endpoint
        for rule in app.url_map.iter_rules()
        if rule.endpoint != "static"
        and not getattr(app.view_functions.get(rule.endpoint), "public_route", False)
        and getattr(app.view_functions.get(rule.endpoint), "required_capability", None) is None
    ]
    if missing_capabilities:
        routes = ", ".join(sorted(set(missing_capabilities)))
        raise RuntimeError("Browser routes without a capability declaration: " + routes)

    return app

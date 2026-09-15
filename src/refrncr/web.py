# Legacy inline templates remain temporarily while pages move to file-based Jinja templates.
# ruff: noqa: E501
from __future__ import annotations

import hmac
import logging
import re
import secrets
from datetime import UTC, date
from decimal import Decimal
from functools import wraps
from urllib.parse import unquote, urlsplit

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

from refrncr.audit_history import AuditHistoryService, filter_values, parse_audit_filters
from refrncr.audit_presentation import ACTION_LABELS, OBJECTS
from refrncr.browser_security import (
    HTTPS_ANONYMOUS_CSRF_COOKIE,
    HTTPS_SESSION_COOKIE,
    BrowserCookiePolicy,
)
from refrncr.dataset_diff import STALE_MESSAGE, DatasetDiffService
from refrncr.dataset_export import ExportGenerationError, logged_export_stream
from refrncr.dataset_versions import DatasetVersionService
from refrncr.datatypes import DATATYPES, get_datatype
from refrncr.diff_contracts import STATUSES, DiffCriteria
from refrncr.login_security import (
    MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS,
    BrowserAuthenticationAdmissionGuard,
    LoginAttemptLimiter,
    SourceIdentityResolver,
    canonical_account_key,
)
from refrncr.ports import AuditReadUnavailable, PersistenceConflictError
from refrncr.presentation_metadata import DATASET_ICONS, key_from_name, rendered_dataset_icon
from refrncr.record_import import (
    MAX_COLUMNS,
    MAX_UPLOAD_BYTES,
    PROFILE_SCAN_ROWS,
    DatasetRecordImportService,
    ImportValidationError,
    distinct_samples,
    parse_source,
)
from refrncr.record_search import (
    MAX_QUERY_BYTES,
    RecordSearchError,
    parse_parameters,
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
from refrncr.source_formats import SourceFormatError, compose_format, formats_for

ANONYMOUS_CSRF_COOKIE = HTTPS_ANONYMOUS_CSRF_COOKIE
SESSION_COOKIE = HTTPS_SESSION_COOKIE
GENERIC_LOGIN_FAILURE = "Invalid credentials"
MAX_BROWSER_FORM_CONTENT_LENGTH = 16 * 1024


def safe_login_target(target: str | None) -> str:
    """Accept only root-relative paths, never URLs or query/fragment payloads."""
    if not target:
        return "/"
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
        return "/"
    parsed = urlsplit(decoded)
    if parsed.scheme or parsed.netloc or parsed.path in {"/login", "/logout", "/setup"}:
        return "/"
    return target


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
    allow_loopback_http: bool = False,
    login_limiter: LoginAttemptLimiter | None = None,
    source_resolver: SourceIdentityResolver | None = None,
    authentication_admission: BrowserAuthenticationAdmissionGuard | None = None,
    audit_history: AuditHistoryService | None = None,
    dataset_versions: DatasetVersionService | None = None,
    review_signing_key: bytes | None = None,
) -> Flask:
    app = Flask(__name__)
    review_secret = (
        review_signing_key if review_signing_key is not None else secrets.token_bytes(32)
    )
    audit_history = audit_history or AuditHistoryService(users.uow)

    app.jinja_env.globals["rendered_dataset_icon"] = rendered_dataset_icon
    app.jinja_env.filters["datatype_label"] = lambda value: get_datatype(value).label

    @app.template_filter("record_value_text")
    def record_value_text(value, data_type):
        return "" if value is None else get_datatype(data_type).text(value)

    @app.template_filter("absolute_utc")
    def absolute_utc(value):
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
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

    app.config["MAX_CONTENT_LENGTH"] = MAX_BROWSER_FORM_CONTENT_LENGTH

    @app.before_request
    def import_upload_limit():
        if request.endpoint == "import_dataset_records" and request.method == "POST":
            request.max_content_length = MAX_UPLOAD_BYTES + 64 * 1024
        elif (
            request.endpoint in {"review_source_formats", "preview_record_import"}
            and request.method == "POST"
        ):
            # Six bounded controls per column, plus CSRF and optional navigation.
            # Multipart framing must fit the existing 250-column upload contract.
            request.max_content_length = MAX_COLUMNS * 1024
            request.max_form_parts = MAX_COLUMNS * 6 + 2

    login_limiter = login_limiter or LoginAttemptLimiter()
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
            g.stale_cookie_settings = settings
        g.authenticated_resolution = resolved
        return resolved

    @app.before_request
    def redirect_anonymous_browser_request():
        # Only registered browser endpoints participate; API/health is owned by FastAPI.
        if request.endpoint and request.endpoint not in {"static", "setup", "login", "logout"}:
            if authenticated() is None:
                target = (
                    safe_login_target(request.path) if request.method in {"GET", "HEAD"} else "/"
                )
                return redirect(url_for("login", next=target))

    def active_navigation() -> str:
        endpoint = request.endpoint or ""
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

    concept_endpoints = {
        "move_dataset_field",
        "new_dataset_field",
        "create_dataset_field",
        "edit_dataset_field",
        "update_dataset_field",
        "delete_dataset_field",
        "new_dataset_record",
        "create_dataset_record",
        "edit_dataset_record",
        "delete_dataset_record",
        "restore_dataset_record",
        "import_dataset_records",
        "dataset_import_step",
        "select_import_worksheet",
        "review_import_mapping",
        "review_source_formats",
        "preview_record_import",
        "confirm_record_import",
        "cancel_record_import",
        "dataset_version_review",
        "dataset_version_record_diff",
        "dataset_version_field_diff",
        "dataset_version_publish",
        "dataset_concept_discard",
    }

    @app.before_request
    def bind_concept_context():
        values = request.view_args or {}
        if (
            dataset_ui_enabled
            and values.get("key")
            and (request.endpoint in concept_endpoints or values.get("concept"))
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

    def render_authenticated_template(template_name: str, **context):
        resolved = authenticated()
        actor = resolved[0] if resolved else None
        csrf = resolved[1].csrf_token if resolved else None
        context.setdefault("current_actor", actor)
        context.setdefault("current_csrf", csrf)
        context.setdefault("active_navigation", active_navigation())
        context.setdefault("dataset_ui_enabled", dataset_ui_enabled)
        return render_template(template_name, **context)

    def require_admin():
        resolved = authenticated()
        if resolved is None or resolved[0].role != "admin":
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
        if endpoint == "dataset_records_view":
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        if (
            endpoint.startswith("audit_")
            or endpoint in concept_endpoints
            or (request.view_args or {}).get("concept", False)
            or endpoint
            in {
                "dataset_version_review",
                "dataset_version_record_diff",
                "dataset_version_field_diff",
            }
            or (endpoint == "dataset_version_publish" and "review" in request.args)
        ):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = (
                "no-referrer" if "review" in request.args else "same-origin"
            )
        return response

    def audit_error(message, status=400):
        return render_authenticated_template("pages/audit/error.html", error=message), status

    @app.get("/settings/audit-log")
    def audit_list():
        actor, _session = require_admin()
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
    def audit_detail(event_id):
        actor, _session = require_admin()
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

    def dataset_route(rule: str, **options):
        if dataset_ui_enabled:
            return app.route(rule, **options)
        return lambda function: function

    def version_errors(function):
        @wraps(function)
        def secured(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except (NotFoundError, AuthorizationError):
                abort(404)
            except HTTPException:
                raise
            except Exception:
                logging.getLogger(__name__).error("Dataset version operation unavailable")
                return "Dataset versions are temporarily unavailable. Reload and try again.", 503

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
    def index():
        resolved = authenticated()
        actor = resolved[0] if resolved else None
        csrf = resolved[1].csrf_token if resolved else None
        return render_template(
            "pages/index.html",
            resolved=resolved,
            current_actor=actor,
            current_csrf=csrf,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    @dataset_route("/datasets", methods=["GET"])
    def dataset_list():
        actor, _login_session = require_authenticated()
        _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
            require_dataset_dependencies()
        )
        visible_datasets = dataset_service.list_dataset_overview(actor.id)
        return render_template(
            "pages/datasets/list.html",
            actor=actor,
            may_create=_authorization.datasets.can_create_dataset(actor.id),
            datasets=visible_datasets,
            deletion_notice=request.args.getlist("deleted") == ["1"],
            current_actor=actor,
            current_csrf=_login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    def render_dataset_create_form(
        *,
        key: str = "",
        name: str = "",
        description: str = "",
        error=None,
        error_field=None,
    ):
        actor, login_session = require_authenticated()
        _auth, service, *_rest = require_dataset_dependencies()
        eligible_teams = _auth.datasets.creation_teams(actor.id)
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
            error=error,
            error_field=error_field,
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    @dataset_route("/datasets/new", methods=["GET"])
    def new_dataset():
        require_dataset_dependencies()
        return render_dataset_create_form()

    @dataset_route("/datasets", methods=["POST"])
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
        return redirect(url_for("dataset_detail", key=dataset.key, concept=True))

    @dataset_route("/datasets/<key>/concept", methods=["GET"], defaults={"concept": True})
    @dataset_route("/datasets/<key>", methods=["GET"], defaults={"concept": False})
    @dataset_route(
        "/datasets/<key>/versions/<version_id>", methods=["GET"], defaults={"concept": False}
    )
    @version_errors
    def dataset_detail(key: str, version_id=None, concept=False):
        return render_dataset_configuration(
            key, version_id=version_id, concept=concept, edit_mode=request.args.get("edit") == "1"
        )

    @dataset_route("/datasets/<key>/configure", methods=["GET"])
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
    ):
        actor, login_session = require_authenticated()
        require_dataset_dependencies()
        field_context_label = None
        if standalone:
            configuration = datasets.get_dataset_configuration(actor.id, key)
            dataset = configuration.dataset
            editable = configuration.may_maintain
            may_govern = configuration.may_govern
            context, state, identifier = None, None, None
            fields, field_states = (), {}
            try:
                field_view = dataset_versions.read(actor.id, key, page=None, prefer_concept=True)
            except NotFoundError:
                field_context_label = "No Current version or active Concept available."
            else:
                fields, identifier = field_view.fields, field_view.identifier
                field_context_label = (
                    f"Current · Version {field_view.state.version.number} · Read only"
                    if field_view.state
                    else "Concept · Read only"
                )
            schema_admin, may_start_concept = False, False
        else:
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
            fields = view.fields
            field_states = {
                change.id: "Edited" if change.status == "Changed" else change.status
                for change in (g.concept_ui.field_changes if concept else ())
            }
            may_start_concept = view.may_maintain and view.concept is None
            if view.state is not None:
                field_context_label = (
                    f"{view.state.status} · Version {view.state.version.number} · Read only"
                )
        if edit_mode and not editable:
            abort(404)
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
            access_teams=access_teams,
            access_users=access_users,
            access_entries=access_entries,
            fields=fields,
            field_states=field_states,
            field_context_label=field_context_label,
            business_key=identifier,
            version_state=state,
        )

    @dataset_route("/datasets/<key>/concept/new", methods=["POST"])
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

    @dataset_route("/datasets/<key>/concept/discard", methods=["GET", "POST"])
    @version_errors
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
                raise NotFoundError("Concept not found") from None
            if identity.actor_id != actor.id or identity.dataset_id != dataset.id:
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

    @dataset_route("/datasets/<key>/versions", methods=["GET"])
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
            raise NotFoundError("Review not found")
        try:
            identity = decode_review(tokens[0], session.id, review_secret)
        except ValueError:
            raise ConflictError("This review link is no longer valid. Reload the review.") from None
        if identity.dataset_id != dataset.id or identity.actor_id != actor.id:
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
        criteria = DiffCriteria(
            tuple(request.args.getlist("status")) or STATUSES,
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

    @dataset_route("/datasets/<key>/versions/review", methods=["GET"])
    @version_errors
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

    @dataset_route("/datasets/<key>/versions/review/records/<object_id>", methods=["GET"])
    @version_errors
    def dataset_version_record_diff(key, object_id):
        return render_diff_detail(key, object_id, "record")

    @dataset_route("/datasets/<key>/versions/review/fields/<object_id>", methods=["GET"])
    @version_errors
    def dataset_version_field_diff(key, object_id):
        return render_diff_detail(key, object_id, "field")

    @dataset_route("/datasets/<key>/versions/publish", methods=["GET", "POST"])
    @version_errors
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
        today = dataset_versions._today().date().isoformat()
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

    @dataset_route("/datasets/<key>/versions/<version_id>/cancel", methods=["GET", "POST"])
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

    def render_dataset_governance(key, mode, error=None):
        actor, login_session = require_authenticated()
        _auth, service, *_rest = require_dataset_dependencies()
        dataset, available_teams, available_users, entries = service.export_access_context(
            actor.id, key
        )
        return render_template(
            "pages/datasets/governance.html",
            dataset=dataset,
            mode=mode,
            teams=available_teams,
            users=available_users,
            entries=entries,
            selected_subject=request.form.get("subject", ""),
            error=error,
            expected_owner_team_id=request.form.get(
                "expected_owner_team_id", dataset.owner_team_id
            ),
            selected_team_id=request.form.get("owner_team_id", dataset.owner_team_id),
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    @dataset_route("/datasets/<key>/owner", methods=["GET", "POST"])
    def dataset_owner(key):
        actor, login_session = require_authenticated()
        _auth, service, *_rest = require_dataset_dependencies()
        if request.method == "POST":
            require_csrf(login_session.csrf_token)
            try:
                expected = {"csrf_token", "expected_owner_team_id", "owner_team_id"}
                if set(request.form) != expected or any(
                    len(request.form.getlist(item)) != 1 for item in expected
                ):
                    raise ValidationError(
                        "Submit exactly one expected and new owning team. Reopen the form."
                    )
                service.change_owner(
                    actor.id,
                    key,
                    request.form["expected_owner_team_id"],
                    request.form["owner_team_id"],
                )
            except (ValidationError, ConflictError) as error:
                return render_dataset_governance(key, "owner", str(error)), 409 if isinstance(
                    error, ConflictError
                ) else 400
            return redirect(url_for("dataset_detail", key=key))
        return render_dataset_governance(key, "owner")

    @dataset_route("/datasets/<key>/export-access", methods=["GET", "POST"])
    def dataset_export_access(key):
        actor, login_session = require_authenticated()
        _auth, service, *_rest = require_dataset_dependencies()
        if request.method == "POST":
            require_csrf(login_session.csrf_token)
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
                return render_dataset_governance(key, "export", str(error)), 409 if isinstance(
                    error, ConflictError
                ) else 400
            if request.args.get("inline") == "1":
                return redirect(
                    url_for(
                        "dataset_configuration"
                        if request.args.get("standalone") == "1"
                        else "dataset_detail",
                        key=key,
                        **(
                            {}
                            if request.args.get("standalone") == "1"
                            else {"concept": request.args.get("concept") == "1"}
                        ),
                        _anchor="access-management",
                    )
                )
            return redirect(url_for("dataset_export_access", key=key))
        return render_dataset_governance(key, "export")

    @dataset_route("/datasets/<key>/export-access/<access_id>/revoke", methods=["POST"])
    def revoke_dataset_export_access(key, access_id):
        actor, login_session = require_authenticated()
        _auth, service, *_rest = require_dataset_dependencies()
        require_csrf(login_session.csrf_token)
        service.revoke_export_access(actor.id, key, access_id)
        if request.args.get("inline") == "1":
            return redirect(
                url_for(
                    "dataset_configuration"
                    if request.args.get("standalone") == "1"
                    else "dataset_detail",
                    key=key,
                    **(
                        {}
                        if request.args.get("standalone") == "1"
                        else {"concept": request.args.get("concept") == "1"}
                    ),
                    _anchor="access-management",
                )
            )
        return redirect(url_for("dataset_export_access", key=key))

    @dataset_route("/datasets/<key>/fields/order", methods=["POST"])
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

    @dataset_route("/datasets/<key>/concept/records", methods=["GET"], defaults={"concept": True})
    @dataset_route("/datasets/<key>/records", methods=["GET"], defaults={"concept": False})
    @dataset_route(
        "/datasets/<key>/versions/<version_id>/records",
        methods=["GET"],
        defaults={"concept": False},
    )
    @version_errors
    def dataset_records_view(key: str, version_id=None, concept=False):
        actor, login_session = require_authenticated()
        require_dataset_dependencies()
        try:
            if len(request.query_string) > MAX_QUERY_BYTES:
                raise RecordSearchError(
                    "Record criteria exceed the URL limit. Clear all to start again."
                )
            parameters, selected_statuses, record_status, requested_page = parse_parameters(
                request.args.items(multi=True)
            )
            if not concept and any(
                name in parameters for name in ("import_results", "status_selection")
            ):
                raise RecordSearchError("Import results require a Concept.")
            if not concept and record_status != "active":
                raise RecordSearchError("Deleted and All are available only in a Concept.")
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
                statuses=(selected_statuses or ("Added", "Changed", "Deleted"))
                if import_results
                else (),
                record_status=record_status,
            )
            criteria_parameters = {
                "q": view.search or None,
                "record_status": record_status if concept else None,
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
            has_criteria=bool(view.search or import_results or record_status != "active"),
            import_unavailable=record_imports.eligibility(actor.id, key) if may_edit else None,
            import_results=import_results,
            import_totals=view.concept.import_result if view.concept and concept else None,
            result_statuses=request.args.getlist("status"),
            may_edit=may_edit,
            may_export=view.may_export,
            version_state=view.state,
            may_configure=may_configure,
            may_start_concept=view.may_maintain and view.concept is None,
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
        "/datasets/<key>/concept/records/export", methods=["GET"], defaults={"concept": True}
    )
    @dataset_route("/datasets/<key>/records/export", methods=["GET"], defaults={"concept": False})
    @dataset_route(
        "/datasets/<key>/versions/<version_id>/records/export",
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
        if len(formats) != 1 or formats[0] not in {"csv", "xlsx", "json"}:
            return "Choose exactly one supported export format", 400
        try:
            artifact = record_service.export_dataset(
                actor.id, key, formats[0], version_id=version_id, prefer_concept=concept
            )
        except NotFoundError:
            abort(404)
        except (ExportGenerationError, ValidationError) as error:
            return str(error), 422
        try:
            response = send_file(
                artifact.content,
                mimetype=artifact.media_type,
                as_attachment=True,
                download_name=artifact.download_name or f"{key}.{artifact.extension}",
                max_age=0,
            )
        except Exception:
            artifact.content.close()
            raise
        response.response = logged_export_stream(response.response)
        response.call_on_close(artifact.content.close)
        return response

    def import_route(rule, **options):
        def decorate(function):
            if getattr(function, "_secured_import", False):
                return dataset_route(rule, **options)(function)

            @wraps(function)
            def secured_import(*args, **kwargs):
                try:
                    return function(*args, **kwargs)
                except (NotFoundError, AuthorizationError):
                    return "The import has expired or is unavailable. Upload the file again.", 404
                except ImportValidationError as error:
                    return render_authenticated_template(
                        "pages/datasets/import/unavailable.html", error=str(error)
                    ), 400

            secured_import._secured_import = True
            return dataset_route(rule, **options)(secured_import)

        return decorate

    def render_import_wizard(dataset, login_session, state=None, *, step=1, error=None, status=200):
        actor, _session = require_authenticated()
        mode = state.mode if state else "new_version"
        fields = dataset_fields.list_fields(actor.id, dataset.key)
        source = assistance = preview = None
        required_keys = ()
        identifier_keys = ()
        mapping_errors = {}
        mapping_samples = []
        format_columns = []
        if state and state.imported_count is None:
            if step >= 3:
                state, source, assistance = record_imports.mapping_assistance(
                    actor.id, login_session.id, dataset.key, state.token
                )
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
                        statuses=tuple(request.args.getlist("status")),
                        search=request.args.get("q", ""),
                    )
                except (NotFoundError, ImportValidationError):
                    raise
                except Exception:
                    return (
                        "Validation could not be completed. No records have been imported. Try again.",
                        503,
                    )
                state = record_imports.state(actor.id, login_session.id, dataset.key, state.token)
                if preview.non_row_errors:
                    step = 3
                    error = " ".join(item.message for item in preview.non_row_errors)
                elif step == 5 and not preview.valid:
                    step = 4
                if step != state.step:
                    state = record_imports.navigate(
                        actor.id, login_session.id, dataset.key, state.token, step
                    )
            if source.headers:
                with record_imports.uow() as uow:
                    identifier = uow.dataset_business_keys.get(dataset.id)
                identifier_keys = identifier.field_keys
                required_keys = tuple(
                    dict.fromkeys(
                        [
                            field.key
                            for field in fields
                            if field.is_required or mode == "new_version"
                        ]
                        + list(identifier.field_keys)
                    )
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
            format_columns = record_imports.format_assistance(
                actor.id, login_session.id, dataset.key, state.token
            )
        if assistance and error:
            choices = assistance.mapping
            keys = {field.key for field in fields}
            for index, choice in enumerate(choices):
                if choice == "":
                    mapping_errors[index] = "Choose a field or Do not import."
                elif choice is not None and choice not in keys:
                    mapping_errors[index] = "Choose a field from this dataset."
                elif choice and choices.count(choice) > 1:
                    mapping_errors[index] = (
                        "This field is mapped more than once. Choose a different field or Do not import."
                    )
        success = state is not None and state.imported_count is not None
        automatic = source is not None and (source.kind == "csv" or len(source.worksheets) == 1)
        completed = {
            1: state is not None,
            2: bool(source and source.headers),
            3: bool(state and state.mapping_ready),
            4: bool(state and state.validation_valid),
            5: success,
        }
        labels = ("Upload file", "Select worksheet", "Map columns", "Preview & validate", "Import")
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
                    if key in identifier.field_keys and key in preview.mapping
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
        back_step = 3 if step == 4 else 4 if step == 5 else 1 if automatic else step - 1
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
        ), status

    def import_redirect(key, state):
        return redirect(
            url_for("dataset_import_step", key=key, token=state.token, step=state.step), code=303
        )

    @import_route(
        "/datasets/<key>/concept/records/import-new-version",
        methods=["GET", "POST"],
    )
    @import_route("/datasets/<key>/records/import", methods=["GET", "POST"])
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

    @import_route("/datasets/<key>/records/import/<token>/step/<int:step>", methods=["GET"])
    def dataset_import_step(key: str, token: str, step: int):
        actor, login_session = require_authenticated()
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        state = record_imports.navigate(actor.id, login_session.id, key, token, step)
        return render_import_wizard(dataset, login_session, state, step=step)

    @import_route("/datasets/<key>/records/import/<token>/worksheet", methods=["POST"])
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

    def submitted_source_formats(mapping, actor, key):
        fields = {field.key: field.data_type for field in dataset_fields.list_fields(actor.id, key)}
        choices = {}
        for index, target in enumerate(mapping):
            profile = request.form.get(f"source_{index}", "")
            components = {"source": profile}
            for part in ("date", "time"):
                name = f"{part}_{index}"
                if name in request.form:
                    components[part] = request.form[name]
            timezone = request.form.get(f"timezone_{index}", "")
            fold = request.form.get(f"fold_{index}", "")
            datatype = fields.get(target)
            if profile in {"text", "native", "offset"}:
                try:
                    profile = compose_format(datatype, components)
                except SourceFormatError:
                    abort(400)
            elif len(components) != 1:
                abort(400)
            if profile and profile not in {p.id for p in formats_for(datatype)}:
                abort(400)
            if (timezone or fold) and (
                datatype != "timestamp" or profile == "v1-offset" or not profile
            ):
                abort(400)
            if fold not in {"", "earlier", "later"}:
                abort(400)
            if target:
                choices[target] = {"profile_id": profile, "timezone": timezone, "fold": fold}
        return choices

    def submitted_component_names(count):
        # Components are optional only for the unchanged canonical-identifier contract.
        return {f"{part}_{index}" for part in ("date", "time") for index in range(count)} & set(
            request.form
        )

    @import_route("/datasets/<key>/records/import/<token>/formats", methods=["POST"])
    def review_source_formats(key: str, token: str):
        actor, login_session = require_authenticated()
        dataset_records.get_dataset_for_add(actor.id, key)
        require_csrf(login_session.csrf_token)
        _state, source, _advice = record_imports.mapping_assistance(
            actor.id, login_session.id, key, token
        )
        expected = {
            f"{prefix}_{index}"
            for prefix in ("mapping", "source", "timezone", "fold")
            for index in range(len(source.headers))
        } | {"csrf_token"}
        expected |= submitted_component_names(len(source.headers))
        if set(request.form) != expected or any(
            len(request.form.getlist(name)) != 1 for name in expected
        ):
            abort(400)
        mapping = tuple(
            None
            if request.form[f"mapping_{index}"] == "__ignored__"
            else request.form[f"mapping_{index}"]
            for index in range(len(source.headers))
        )
        columns = record_imports.format_assistance(
            actor.id,
            login_session.id,
            key,
            token,
            mapping,
            submitted_source_formats(mapping, actor, key),
        )
        return render_template(
            "pages/datasets/import/source_format_fragments.html", format_columns=columns
        )

    @import_route("/datasets/<key>/records/import/<token>/mapping", methods=["GET"])
    def review_import_mapping(key: str, token: str):
        return dataset_import_step(key, token, 3)

    @import_route("/datasets/<key>/records/import/<token>/preview", methods=["GET", "POST"])
    def preview_record_import(key: str, token: str):
        actor, login_session = require_authenticated()
        dataset = dataset_records.get_dataset_for_add(actor.id, key)
        state, source, _assistance = record_imports.mapping_assistance(
            actor.id, login_session.id, key, token
        )
        if request.method == "GET":
            return dataset_import_step(key, token, 4)
        require_csrf(login_session.csrf_token)
        expected = {f"mapping_{index}" for index in range(len(source.headers))} | {"csrf_token"}
        format_names = {
            f"{prefix}_{index}"
            for prefix in ("source", "timezone", "fold")
            for index in range(len(source.headers))
        }
        if set(request.form) & format_names:
            expected |= format_names
            expected |= submitted_component_names(len(source.headers))
        target_step = request.form.get("go_step")
        if target_step is not None:
            if len(request.form.getlist("go_step")) != 1 or target_step not in {"1", "2", "4", "5"}:
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
            for index in range(len(source.headers))
        )
        choices = (
            submitted_source_formats(mapping, actor, key)
            if set(request.form) & format_names
            else None
        )
        try:
            preview = record_imports.preview(
                actor.id, login_session.id, key, token, mapping, choices
            )
        except NotFoundError:
            raise
        except Exception:
            state = record_imports.state(actor.id, login_session.id, key, token)
            return render_import_wizard(
                dataset,
                login_session,
                state,
                step=3,
                error="Validation could not be completed. No records have been imported. Try again.",
                status=503,
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
                error=" ".join(item.message for item in preview.non_row_errors),
                status=400,
            )
        if target_step == "5" and preview.valid:
            state = record_imports.navigate(actor.id, login_session.id, key, token, 5)
        return import_redirect(key, state)

    @import_route("/datasets/<key>/records/import/<token>/confirm", methods=["POST"])
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
                error="The import changed since validation. No records have been imported. Review the validation results before continuing.",
                status=409,
            )
        except Exception:
            state = record_imports.state(actor.id, login_session.id, key, token)
            return render_import_wizard(
                dataset,
                login_session,
                state,
                step=5,
                error="The import could not be completed. No records have been imported. Try again.",
                status=503,
            )
        if state.mode == "new_version":
            return redirect(
                url_for("dataset_records_view", key=key, concept=True, import_results=1), code=303
            )
        return import_redirect(key, record_imports.state(actor.id, login_session.id, key, token))

    @import_route("/datasets/<key>/records/import/<token>/cancel", methods=["POST"])
    def cancel_record_import(key: str, token: str):
        actor, login_session = require_authenticated()
        require_csrf(login_session.csrf_token)
        if set(request.form) != {"csrf_token"} or len(request.form.getlist("csrf_token")) != 1:
            abort(400)
        record_imports.cancel(actor.id, login_session.id, key, token)
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
        response = render_authenticated_template(
            "pages/datasets/records/form.html",
            dataset=dataset,
            fields=fields,
            current_csrf=csrf,
            submitted=submitted,
            errors=errors,
            record=record,
        )
        return (response, status) if status != 200 else response

    @dataset_route("/datasets/<key>/records/new", methods=["GET"])
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
        return render_record_form(dataset, fields, csrf=login_session.csrf_token)

    @dataset_route("/datasets/<key>/records", methods=["POST"])
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
                errors={"_record": str(error)},
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

    @dataset_route("/datasets/<key>/records/<record_id>/edit", methods=["GET", "POST"])
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

    @dataset_route("/datasets/<key>/concept/records/<record_id>/restore", methods=["POST"])
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

    @dataset_route("/datasets/<key>/records/<record_id>/delete", methods=["GET", "POST"])
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

    @dataset_route("/datasets/<key>/edit", methods=["GET"])
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

    @dataset_route("/datasets/<key>", methods=["POST"])
    def update_dataset(key: str):
        actor, login_session = require_authenticated()
        _authorization, dataset_service, _field_service, _business_key_service, _record_service = (
            require_dataset_dependencies()
        )
        require_csrf(login_session.csrf_token)
        name = request.form.get("name", "")
        description = request.form.get("description", "")
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

    @dataset_route("/datasets/id/<dataset_id>/delete", methods=["GET", "POST"])
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
        if not authorization_service.check_access(actor_id, "dataset", dataset.id, "admin"):
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
        )

    @dataset_route("/datasets/<key>/fields/new", methods=["GET"])
    def new_dataset_field(key: str):
        actor, login_session = require_authenticated()
        dataset = require_dataset_schema_admin(actor.id, key)
        return render_dataset_field_create_form(dataset, csrf=login_session.csrf_token)

    @dataset_route("/datasets/<key>/fields", methods=["POST"])
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
            error=error,
            current_csrf=csrf,
            type_choices=tuple(DATATYPES.values()),
        )

    @dataset_route("/datasets/<key>/fields/<field_key>/edit", methods=["GET"])
    def edit_dataset_field(key: str, field_key: str):
        actor, login_session = require_authenticated()
        dataset = require_dataset_schema_admin(actor.id, key)
        _authorization, _dataset_service, field_service, business_key_service, record_service = (
            require_dataset_dependencies()
        )
        try:
            field = field_service.get_field(actor.id, key, field_key)
            business_key = business_key_service.get_business_key(actor.id, key)
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
        )

    @dataset_route("/datasets/<key>/fields/<field_key>", methods=["POST"])
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
                definition=request.form.get("definition", ""),
                unit=request.form.get("unit", ""),
            )
        except NotFoundError:
            abort(404)
        except ValidationError as error:
            dataset = require_dataset_schema_admin(actor.id, key)
            try:
                field = field_service.get_field(actor.id, key, field_key)
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
                    error=str(error),
                ),
                400,
            )
        return redirect(
            url_for("dataset_detail", key=key, concept=True, tab="fields", _anchor="fields")
        )

    @dataset_route("/datasets/<key>/fields/<field_key>/delete", methods=["POST"])
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
                    error=str(error),
                ),
                400,
            )
        return redirect(
            url_for("dataset_detail", key=key, concept=True, tab="fields", _anchor="fields")
        )

    @app.route("/setup", methods=["GET", "POST"])
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
    def login():
        settings = require_cookie_settings()
        return_target = safe_login_target(
            request.form.get("next") if request.method == "POST" else request.args.get("next")
        )

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
                return invalid_credentials_response()
            if oversized_identifier:
                attempt.failed()
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
                return invalid_credentials_response()
            try:
                with permit:
                    principal = authentication.authenticate(submitted_email, submitted_password)
            except Exception:
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
            return invalid_credentials_response()
        return anonymous_csrf_form_response(
            "pages/auth/login.html", next_target=return_target, field_errors={}, email=None
        )

    @app.post("/logout")
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
        sessions.revoke(token)
        response = redirect(url_for("index"))
        response.delete_cookie(settings.session_name, **settings.common_options)
        return response

    @app.get("/users")
    def user_list():
        actor, login_session = require_admin()
        managed_users = users.list_users(actor.id)
        user_teams = {}
        for relationship in memberships.list_relationships(actor.id):
            user_teams.setdefault(relationship.user_id, []).append(relationship)
        return render_template(
            "pages/users/list.html",
            users=managed_users,
            user_teams=user_teams,
            open_user=request.args.get("open_user"),
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    @app.post("/users")
    def create_user():
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        users.create_user(
            actor.id,
            request.form["email"],
            request.form["display_name"],
            request.form["password"],
            request.form.get("role", "user"),
        )
        return redirect(url_for("user_list"))

    @app.post("/users/<target_id>/<action>")
    def change_user_state(target_id: str, action: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        operations = {
            "disable": users.disable_user,
            "enable": users.enable_user,
            "delete": users.delete_user,
        }
        operation = operations.get(action)
        if operation is None:
            abort(404)
        operation(actor.id, target_id)
        return redirect(url_for("user_list"))

    @app.post("/users/<target_id>/email")
    def change_user_email(target_id: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        users.change_email(actor.id, target_id, request.form["email"])
        return redirect(url_for("user_list"))

    @app.get("/teams")
    def team_list():
        actor, login_session = require_authenticated()
        if actor.role != "admin":
            return render_template(
                "pages/teams/readonly.html",
                teams=teams.list_teams(actor.id),
                current_actor=actor,
                current_csrf=login_session.csrf_token,
                active_navigation=active_navigation(),
                dataset_ui_enabled=dataset_ui_enabled,
            )
        managed_teams = teams.list_teams(actor.id)
        team_users = {}
        for relationship in memberships.list_relationships(actor.id):
            team_users.setdefault(relationship.team_id, []).append(relationship)
        open_team = request.args.get("open_team")
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
            current_actor=actor,
            current_csrf=login_session.csrf_token,
            active_navigation=active_navigation(),
            dataset_ui_enabled=dataset_ui_enabled,
        )

    @app.post("/teams")
    def create_team():
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        teams.create_team(actor.id, request.form["name"])
        return redirect(url_for("team_list"))

    @app.post("/teams/<team_id>/rename")
    def rename_team(team_id: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        teams.rename_team(actor.id, team_id, request.form["name"])
        return redirect(url_for("team_list"))

    @app.post("/teams/<team_id>/delete")
    def delete_team(team_id: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        teams.delete_team(actor.id, team_id)
        return redirect(url_for("team_list"))

    @app.get("/teams/<team_id>/members")
    def team_members(team_id: str):
        actor, login_session = require_admin()
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
    def create_membership(team_id: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        memberships.create_membership(actor.id, request.form["user_id"], team_id)
        return redirect(url_for("team_list", open_team=team_id))

    @app.post("/teams/<team_id>/members/<membership_id>/delete")
    def delete_membership(team_id: str, membership_id: str):
        actor, login_session = require_admin()
        require_csrf(login_session.csrf_token)
        memberships.delete_membership(actor.id, team_id, membership_id)
        return redirect(url_for("team_list", open_team=team_id))

    @app.errorhandler(UserManagementError)
    def handle_user_error(error: UserManagementError):
        if isinstance(error, NotFoundError) and request.endpoint in {
            "dataset_owner",
            "dataset_export_access",
            "revoke_dataset_export_access",
        }:
            return "Dataset not found", 404
        status = 403 if isinstance(error, AuthorizationError) else 400
        return str(error), status

    return app

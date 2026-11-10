"""Compose the Community browser application from domain blueprints.

Each blueprint exposes module-level route functions and stores its typed service
context in ``app.extensions["refrncr.<blueprint>"]``. A
``register_<blueprint>_blueprint(app, ...)`` function creates that context and
registers the blueprint. New route modules must follow this pattern so routes
remain independently importable without introducing module-global services.
Large blueprints or subpackages may expose services through module-level
``LocalProxy`` names when that keeps a route move substantially literal. Those
proxies must still resolve through the blueprint's typed context in
``app.extensions`` and may only be used inside an application or request
context, never while importing a module.
The module-level :class:`~flask.Blueprint` object is always named ``blueprint``;
the audit guard relies on this shared name when discovering registered routes.
"""

from __future__ import annotations

import hmac
import re
import secrets
from datetime import UTC, date, datetime
from functools import wraps
from uuid import uuid4

from flask import (
    Flask,
    abort,
    g,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from markupsafe import Markup

from refrncr.api_keys import LIFETIME, ApiKeyService
from refrncr.audit import record_security_event
from refrncr.audit_history import MAX_AUDIT_DATETIME_LENGTH, AuditHistoryService
from refrncr.browser_security import (
    HTTPS_ANONYMOUS_CSRF_COOKIE,
    HTTPS_SESSION_COOKIE,
    BrowserCookiePolicy,
)
from refrncr.change_status import ChangeStatus
from refrncr.clock import SYSTEM_CLOCK, Clock
from refrncr.dataset_diff import DatasetDiffService
from refrncr.dataset_versions import DatasetVersionService
from refrncr.datatypes import get_datatype
from refrncr.diff_contracts import MAX_SEARCH
from refrncr.hierarchy import (
    MAX_HIERARCHY_NODE_NAME,
    HierarchyService,
)
from refrncr.hierarchy_import import HierarchyImportService
from refrncr.installation_policy import EXPORT_POLICIES
from refrncr.installation_settings import InstallationSettingsService, PolicyConfirmationRequired
from refrncr.login_security import (
    BrowserAuthenticationAdmissionGuard,
    LoginAttemptLimiter,
    SourceIdentityResolver,
)
from refrncr.overview import OverviewService
from refrncr.ports import PersistenceConflictError
from refrncr.presentation import (
    DISPLAY_STATUSES,
    dataset_kind_label,
    pluralize,
    status_badge_variant,
    status_label,
)
from refrncr.presentation_metadata import (
    MAX_DATASET_ICON_LENGTH,
    MAX_FIELD_DEFINITION,
    MAX_FIELD_UNIT,
    dataset_icon_sprite,
    default_dataset_icon,
    rendered_dataset_icon,
)
from refrncr.record_import import (
    MAX_UPLOAD_BYTES,
    DatasetRecordImportService,
    ImportExpiredError,
    ImportValidationError,
)
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
    SessionService,
    StaleRecordError,
    TeamService,
    UserManagementError,
    UserService,
    ValidationError,
    parse_expected_revision,
)
from refrncr.source_formats import MAX_SOURCE_TIMEZONE_LENGTH
from refrncr.web.audit import register_audit_blueprint
from refrncr.web.auth import register_auth_blueprint
from refrncr.web.datasets import register_datasets_blueprint
from refrncr.web.errors import register_error_handlers
from refrncr.web.helpers import (
    safe_login_target as safe_login_target,
)
from refrncr.web.hierarchies import register_hierarchies_blueprint
from refrncr.web.hierarchies.views import render_hierarchy
from refrncr.web.hooks import (
    register_concept_hooks,
    register_dataset_visit_hook,
    register_request_limit_hook,
    register_response_hooks,
    register_security_hooks,
)
from refrncr.web.imports import register_imports_blueprint
from refrncr.web.overview import register_overview_blueprint
from refrncr.web.records import register_records_blueprint
from refrncr.web.routing import (
    authenticated_route,
    capability_route,
    dataset_route,
    no_store_route,
    register_web_routing_context,
)
from refrncr.web.routing import public_route as public_route
from refrncr.web.teams import register_teams_blueprint
from refrncr.web.users import register_users_blueprint
from refrncr.web.versions import register_versions_blueprint
from refrncr.xml_security import require_xml_protection

ANONYMOUS_CSRF_COOKIE = HTTPS_ANONYMOUS_CSRF_COOKIE
SESSION_COOKIE = HTTPS_SESSION_COOKIE
GENERIC_LOGIN_FAILURE = "Invalid credentials"
MAX_BROWSER_FORM_CONTENT_LENGTH = 16 * 1024


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
    require_xml_protection()
    app = Flask(__name__, static_folder="../static", template_folder="../templates")
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
    app.jinja_env.globals["non_login_form_attrs"] = lambda: Markup(
        "autocomplete='off' data-form-type='other' data-1p-ignore "
        "data-lpignore='true' data-bwignore='true'"
    )
    app.jinja_env.globals["non_login_field_attrs"] = lambda: Markup(
        "data-form-type='other' data-1p-ignore data-lpignore='true' data-bwignore='true'"
    )
    app.jinja_env.globals["dataset_icon_sprite"] = dataset_icon_sprite
    app.jinja_env.globals["default_dataset_icon"] = default_dataset_icon
    app.jinja_env.filters["datatype_label"] = lambda value: get_datatype(value).label
    app.jinja_env.filters["dataset_title"] = lambda value: value.replace("->", "→")
    app.jinja_env.filters["status_label"] = status_label
    app.jinja_env.filters["status_badge_variant"] = status_badge_variant
    app.jinja_env.filters["dataset_kind_label"] = dataset_kind_label
    app.jinja_env.filters["pluralize"] = pluralize
    app.jinja_env.globals["dataset_kind_label"] = dataset_kind_label
    app.jinja_env.globals["ChangeStatus"] = ChangeStatus
    app.jinja_env.globals["DISPLAY_STATUSES"] = DISPLAY_STATUSES
    app.jinja_env.globals["status_label"] = status_label
    app.jinja_env.globals.update(
        MAX_AUDIT_DATETIME_LENGTH=MAX_AUDIT_DATETIME_LENGTH,
        MAX_DATASET_ICON_LENGTH=MAX_DATASET_ICON_LENGTH,
        MAX_HIERARCHY_NODE_NAME=MAX_HIERARCHY_NODE_NAME,
        MAX_SEARCH=MAX_SEARCH,
        MAX_SOURCE_TIMEZONE_LENGTH=MAX_SOURCE_TIMEZONE_LENGTH,
        MAX_FIELD_DEFINITION=MAX_FIELD_DEFINITION,
        MAX_FIELD_UNIT=MAX_FIELD_UNIT,
    )

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

    register_request_limit_hook(app)

    login_limiter = login_limiter or LoginAttemptLimiter(clock=clock)
    source_resolver = source_resolver or SourceIdentityResolver()
    authentication_admission = authentication_admission or BrowserAuthenticationAdmissionGuard()
    existing_dataset_dependencies = (
        authorization,
        datasets,
        dataset_fields,
        dataset_business_keys,
    )
    hierarchy = None
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
    overview_service = (
        OverviewService(datasets.uow, authorization.datasets) if dataset_ui_enabled else None
    )
    dataset_diff = DatasetDiffService(dataset_versions) if dataset_ui_enabled else None
    installation_settings = (
        InstallationSettingsService(users.uow, authorization.datasets) if authorization else None
    )
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

    register_response_hooks(app)

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

    register_security_hooks(
        app,
        authenticated=authenticated,
        cookie_settings=cookie_settings,
        require_csrf=lambda token: require_csrf(token),
        require_capability=lambda capability: require_capability(capability),
        authorization=authorization,
        datasets=datasets,
    )

    def active_navigation() -> str:
        endpoint = request.endpoint or ""
        if endpoint.startswith("api_key_"):
            return "api_keys"
        if endpoint.startswith("audit."):
            return "audit"
        if endpoint == "overview.index":
            return "home"
        if endpoint.startswith(
            ("dataset_", "datasets.", "hierarchies.", "imports.", "records.", "versions.")
        ):
            return "datasets"
        if endpoint.startswith("users."):
            return "users"
        if endpoint.startswith("teams."):
            return "teams"
        return ""

    register_concept_hooks(
        app,
        dataset_ui_enabled=dataset_ui_enabled,
        require_authenticated=lambda: require_authenticated(),
        require_csrf=lambda token: require_csrf(token),
        datasets=datasets,
        dataset_versions=dataset_versions,
        render_authenticated_template=lambda template, **context: render_authenticated_template(
            template, **context
        ),
        authenticated=authenticated,
    )

    def render_authenticated_template(template_name: str, **context):
        resolved = authenticated()
        actor = resolved[0] if resolved else None
        csrf = resolved[1].csrf_token if resolved else None
        context.setdefault("current_actor", actor)
        context.setdefault("current_csrf", csrf)
        context.setdefault("active_navigation", active_navigation())
        context.setdefault("dataset_ui_enabled", dataset_ui_enabled)
        return render_template(template_name, **context)

    def render_mapping_template(template_name, **context):
        resolved = authenticated()
        actor = resolved[0] if resolved else None
        dataset = context.get("dataset")
        if (
            dataset is not None
            and actor
            and template_name
            in {
                "pages/datasets/detail.html",
                "pages/datasets/records.html",
                "pages/datasets/hierarchy.html",
                "pages/datasets/versions/review.html",
            }
        ):
            context["mapping_usage"] = datasets.mapping_usage(
                actor.id,
                dataset.key,
                details=template_name
                in {"pages/datasets/detail.html", "pages/datasets/versions/review.html"},
            )
        if (
            dataset is not None
            and dataset.kind == "mapping"
            and actor
            and context.get("source_context_available", True)
        ):
            state = context.get("version_state")
            history = context.get("history")
            if history and not history.may_maintain:
                if not history.entries:
                    return render_authenticated_template(template_name, **context)
                state = history.entries[0]
            context["mapping_source"] = datasets.mapping_source(
                actor.id,
                dataset.key,
                state.version.id if state else None,
                history_version_ids=tuple(entry.version.id for entry in history.entries)
                if history
                else (),
            )
            if history:
                context["mapping_history_sources"] = context["mapping_source"]["history"]
        return render_authenticated_template(template_name, **context)

    register_error_handlers(
        app,
        render_authenticated_template,
        generic_login_failure=GENERIC_LOGIN_FAILURE,
    )

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
            keys=api_keys.list_keys(actor.id),
            revoked=request.args.get("revoked") == "1",
            requires_export_access=(
                installation_settings is not None
                and installation_settings.requires_export_access(actor.id)
            ),
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

    register_audit_blueprint(
        app,
        audit_history=audit_history,
        require_capability=require_capability,
        render_authenticated_template=render_authenticated_template,
    )

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

    register_web_routing_context(
        app,
        dataset_ui_enabled=dataset_ui_enabled,
        audit_security=audit_security,
        render_authenticated_template=render_authenticated_template,
    )

    register_overview_blueprint(
        app,
        authenticated=authenticated,
        require_authenticated=require_authenticated,
        overview_service=overview_service,
        render_authenticated_template=render_authenticated_template,
        audit_security=audit_security,
        dataset_ui_enabled=dataset_ui_enabled,
    )

    register_dataset_visit_hook(
        app,
        dataset_ui_enabled=dataset_ui_enabled,
        authenticated=authenticated,
        overview_service=overview_service,
    )

    register_datasets_blueprint(
        app,
        authorization=authorization,
        datasets=datasets,
        dataset_fields=dataset_fields,
        dataset_business_keys=dataset_business_keys,
        dataset_records=dataset_records,
        dataset_versions=dataset_versions,
        require_authenticated=require_authenticated,
        require_csrf=require_csrf,
        render_authenticated_template=render_authenticated_template,
        render_mapping_template=render_mapping_template,
        active_navigation=active_navigation,
        audit_security=audit_security,
    )

    register_versions_blueprint(
        app,
        datasets=datasets,
        dataset_versions=dataset_versions,
        dataset_diff=dataset_diff,
        record_imports=record_imports,
        review_secret=review_secret,
        require_authenticated=require_authenticated,
        require_csrf=require_csrf,
        render_authenticated_template=render_authenticated_template,
        render_mapping_template=render_mapping_template,
        audit_security=audit_security,
    )

    def exact_expected_revision():
        values = request.form.getlist("expected_revision")
        if len(values) != 1:
            raise ValidationError("Submit exactly one expected revision")
        return parse_expected_revision(values[0])

    register_hierarchies_blueprint(
        app,
        authorization=authorization,
        dataset_versions=dataset_versions,
        hierarchy=hierarchy,
        require_authenticated=require_authenticated,
        render_authenticated_template=render_authenticated_template,
        exact_expected_revision=exact_expected_revision,
    )

    register_records_blueprint(
        app,
        authorization=authorization,
        datasets=datasets,
        dataset_fields=dataset_fields,
        dataset_business_keys=dataset_business_keys,
        dataset_records=dataset_records,
        dataset_versions=dataset_versions,
        record_imports=record_imports,
        require_authenticated=require_authenticated,
        require_csrf=require_csrf,
        render_authenticated_template=render_authenticated_template,
        render_mapping_template=render_mapping_template,
        render_hierarchy=render_hierarchy,
        active_navigation=active_navigation,
        exact_expected_revision=exact_expected_revision,
    )

    register_imports_blueprint(
        app,
        dataset_records=dataset_records,
        record_imports=record_imports,
        require_authenticated=require_authenticated,
        require_csrf=require_csrf,
        require_dataset_dependencies=require_dataset_dependencies,
        render_authenticated_template=render_authenticated_template,
        audit_security=audit_security,
    )

    def import_route(rule, *, capability: str, **options):
        def decorate(function):
            function.required_capability = capability
            if getattr(function, "_secured_import", False):
                return dataset_route(
                    app,
                    rule,
                    capability=capability,
                    **options,
                )(function)

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
                        return_url=url_for(
                            "imports.import_dataset_records", key=kwargs.get("key", "")
                        ),
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
                except ConflictError as error:
                    return render_authenticated_template(
                        "pages/datasets/import/unavailable.html",
                        error=str(error),
                        return_url=url_for(
                            "imports.import_dataset_records", key=kwargs.get("key", "")
                        ),
                    ), 409
                except ImportValidationError as error:
                    return render_authenticated_template(
                        "pages/datasets/import/unavailable.html", error=str(error)
                    ), 400

            secured_import._secured_import = True
            return dataset_route(
                app,
                rule,
                capability=capability,
                **options,
            )(secured_import)

        return decorate

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
        return redirect(url_for("records.dataset_records_view", key=key, concept=True), code=303)

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
        try:
            hierarchy_imports.cancel(actor.id, login_session.id, key, token)
        except ConflictError as error:
            dataset = dataset_records.get_dataset_for_add(actor.id, key)
            state = hierarchy_imports.active(actor.id, login_session.id, key)
            return render_authenticated_template(
                "pages/datasets/hierarchy_import.html",
                dataset=dataset,
                state=state,
                preview=None,
                current_csrf=login_session.csrf_token,
                error=str(error),
            ), 409
        return redirect(url_for("records.dataset_records_view", key=key, concept=True), code=303)

    register_auth_blueprint(
        app,
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
        generic_login_failure=GENERIC_LOGIN_FAILURE,
    )

    @app.route("/settings/dataset-access", methods=["GET", "POST"])
    @capability_route("can_administer_installation")
    def dataset_access_settings():
        actor, login_session = require_capability("can_administer_installation")
        require_dataset_dependencies()
        policy = installation_settings.get_policy(actor.id)
        expected, selected = policy, policy
        error, confirmation, status = None, None, 200
        if request.method == "POST":
            require_csrf(login_session.csrf_token)
            expected = request.form.get("expected_policy", "")
            selected = request.form.get("policy", "")
            try:
                installation_settings.change_policy(
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
                return redirect(url_for("dataset_access_settings", notice="saved"), code=303)
        return render_authenticated_template(
            "pages/settings/dataset_access.html",
            active_navigation="dataset_access",
            policies=EXPORT_POLICIES,
            selected=selected,
            expected=expected,
            error=error,
            confirmation=confirmation,
            current_csrf=login_session.csrf_token,
        ), status

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

    register_users_blueprint(
        app,
        users=users,
        memberships=memberships,
        require_capability=require_capability,
        require_csrf=require_csrf,
        active_navigation=active_navigation,
        dataset_ui_enabled=dataset_ui_enabled,
    )

    register_teams_blueprint(
        app,
        teams=teams,
        memberships=memberships,
        require_authenticated=require_authenticated,
        require_capability=require_capability,
        require_csrf=require_csrf,
        active_navigation=active_navigation,
        dataset_ui_enabled=dataset_ui_enabled,
    )

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

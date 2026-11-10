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
import secrets

from flask import (
    Flask,
    abort,
    g,
    make_response,
    render_template,
    request,
)

from refrncr.api_keys import ApiKeyService
from refrncr.audit import record_security_event
from refrncr.audit_history import AuditHistoryService
from refrncr.browser_security import (
    HTTPS_ANONYMOUS_CSRF_COOKIE,
    HTTPS_SESSION_COOKIE,
    BrowserCookiePolicy,
)
from refrncr.clock import SYSTEM_CLOCK, Clock
from refrncr.dataset_diff import DatasetDiffService
from refrncr.dataset_versions import DatasetVersionService
from refrncr.hierarchy import HierarchyService
from refrncr.hierarchy_import import HierarchyImportService
from refrncr.installation_settings import InstallationSettingsService
from refrncr.login_security import (
    BrowserAuthenticationAdmissionGuard,
    LoginAttemptLimiter,
    SourceIdentityResolver,
)
from refrncr.overview import OverviewService
from refrncr.record_import import DatasetRecordImportService
from refrncr.services import (
    AuthenticationService,
    AuthorizationService,
    DatasetBusinessKeyService,
    DatasetFieldService,
    DatasetRecordService,
    DatasetService,
    MembershipService,
    SessionService,
    TeamService,
    UserService,
)
from refrncr.web.audit import register_audit_blueprint
from refrncr.web.auth import register_auth_blueprint
from refrncr.web.datasets import register_datasets_blueprint
from refrncr.web.errors import register_error_handlers, register_user_error_handler
from refrncr.web.filters import register_template_filters
from refrncr.web.helpers import (
    exact_expected_revision,
)
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
from refrncr.web.navigation import active_navigation
from refrncr.web.overview import register_overview_blueprint
from refrncr.web.records import register_records_blueprint
from refrncr.web.routing import authenticated_route as authenticated_route
from refrncr.web.routing import public_route as public_route
from refrncr.web.routing import register_web_routing_context
from refrncr.web.settings import register_settings_blueprint
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

    register_template_filters(app)

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
        record_imports=record_imports,
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

    register_hierarchies_blueprint(
        app,
        authorization=authorization,
        dataset_versions=dataset_versions,
        hierarchy=hierarchy,
        hierarchy_imports=hierarchy_imports,
        dataset_records=dataset_records,
        require_authenticated=require_authenticated,
        require_csrf=require_csrf,
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

    register_settings_blueprint(
        app,
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

    register_user_error_handler(app, audit_security)

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

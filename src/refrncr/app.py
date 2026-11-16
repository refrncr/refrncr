import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from a2wsgi import WSGIMiddleware
from fastapi import FastAPI, Request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from refrncr.api.documentation import publish_documentation
from refrncr.api.routes import create_api
from refrncr.audit_history import AuditHistoryService
from refrncr.authorization import InstallationSettingsService
from refrncr.clock import SYSTEM_CLOCK, Clock
from refrncr.datasets.dataset_diff import DatasetDiffService
from refrncr.hierarchy import HierarchyService
from refrncr.hierarchy_import import HierarchyImportService
from refrncr.identity.api_keys import ApiKeyService, configured_api_keys
from refrncr.identity.login_security import SourceIdentityResolver
from refrncr.operational import (
    LOGGER_NAME,
    OperationalMiddleware,
    operational_response,
    readiness_status,
)
from refrncr.overview import OverviewService
from refrncr.persistence import SqlAlchemyPermissionDataSource, SqlAlchemyUnitOfWorkFactory
from refrncr.record_import import DatasetRecordImportService
from refrncr.services import (
    AuthenticationService,
    AuthorizationService,
    DatasetAuthorizationService,
    DatasetBusinessKeyService,
    DatasetFieldService,
    DatasetRecordService,
    DatasetResourceResolver,
    DatasetService,
    GrantService,
    MembershipService,
    ResourceTypeRegistry,
    SessionService,
    TeamService,
    UserService,
    create_deprecated_grant_service,
)
from refrncr.versions import DatasetVersionService
from refrncr.web import create_flask_app
from refrncr.xml_security import require_xml_protection


@dataclass(frozen=True, slots=True)
class ServiceWrappers:
    """Optional integrator wrappers applied once during Community composition."""

    users: Callable[[UserService], UserService] | None = None
    datasets: Callable[[DatasetService], DatasetService] | None = None


@dataclass(frozen=True)
class CommunityServices:
    installation_settings: InstallationSettingsService
    api_keys: ApiKeyService | None
    dataset_versions: DatasetVersionService
    audit_history: AuditHistoryService
    users: UserService
    authentication: AuthenticationService
    sessions: SessionService
    teams: TeamService
    memberships: MembershipService
    _deprecated_grants: Callable[[], GrantService]
    authorization: AuthorizationService
    datasets: DatasetService
    dataset_fields: DatasetFieldService
    dataset_business_keys: DatasetBusinessKeyService
    dataset_records: DatasetRecordService
    record_imports: DatasetRecordImportService
    hierarchy_imports: HierarchyImportService
    hierarchy: HierarchyService
    overview: OverviewService
    dataset_diff: DatasetDiffService

    @property
    def grants(self) -> GrantService:
        """Deprecated test/programmatic compatibility access to historical Grants."""
        return self._deprecated_grants()


def create_community_services(
    sessions: sessionmaker[Session],
    resources: ResourceTypeRegistry,
    *,
    clock: Clock = SYSTEM_CLOCK,
    wrappers: ServiceWrappers | None = None,
) -> CommunityServices:
    """Compose shared services with the Community SQLAlchemy adapters."""
    uow = SqlAlchemyUnitOfWorkFactory(sessions, clock)
    permission_data = SqlAlchemyPermissionDataSource(sessions)
    resources.register("dataset", DatasetResourceResolver(uow))
    authorization = AuthorizationService(
        permission_data, resources, DatasetAuthorizationService(uow)
    )
    versions = DatasetVersionService(uow, authorization.datasets, clock=clock)
    dataset_records = DatasetRecordService(uow, authorization, versions=versions)
    wrappers = wrappers or ServiceWrappers()
    users = UserService(uow)
    datasets = DatasetService(uow, authorization, versions=versions)
    if wrappers.users is not None:
        users = wrappers.users(users)
    if wrappers.datasets is not None:
        datasets = wrappers.datasets(datasets)
    final_versions = datasets.versions
    services = CommunityServices(
        installation_settings=InstallationSettingsService(uow, authorization.datasets),
        api_keys=configured_api_keys(uow, clock=clock),
        dataset_versions=final_versions,
        audit_history=AuditHistoryService(uow, clock=clock),
        users=users,
        authentication=AuthenticationService(uow),
        sessions=SessionService(sessions, clock=clock),
        teams=TeamService(uow),
        memberships=MembershipService(uow),
        _deprecated_grants=lambda: create_deprecated_grant_service(uow, resources),
        authorization=authorization,
        datasets=datasets,
        dataset_fields=DatasetFieldService(uow, authorization, versions=versions),
        dataset_business_keys=DatasetBusinessKeyService(uow, authorization, versions=versions),
        dataset_records=dataset_records,
        record_imports=DatasetRecordImportService(dataset_records),
        hierarchy_imports=HierarchyImportService(uow, authorization),
        hierarchy=HierarchyService(uow, authorization, versions=versions),
        overview=OverviewService(uow, authorization.datasets),
        dataset_diff=DatasetDiffService(final_versions),
    )
    return services


def create_application(
    services: CommunityServices,
    *,
    allow_loopback_http: bool = False,
    trusted_proxies: tuple[str, ...] = (),
    readiness_engine: Engine | None = None,
) -> FastAPI:
    require_xml_protection()
    services.users.bootstrap_organisation()

    @asynccontextmanager
    async def lifespan(_app):
        logging.getLogger(LOGGER_NAME).info(
            "Application started", extra={"event": "application.started"}
        )
        yield
        logging.getLogger(LOGGER_NAME).info(
            "Application stopped", extra={"event": "application.stopped"}
        )

    app = FastAPI(
        title="refrncr",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(OperationalMiddleware)
    clock = getattr(services.api_keys, "clock", getattr(services.users.uow, "clock", SYSTEM_CLOCK))
    api = create_api(services.dataset_versions, services.api_keys, clock=clock)
    app.mount("/api/v1", api)
    publish_documentation(app, api)

    @app.get("/healthz", include_in_schema=False)
    def health(request: Request):
        return operational_response("ok", request.scope["refrncr.request_id"])

    @app.get("/api/health", include_in_schema=False)
    def legacy_health(request: Request):
        return operational_response("ok", request.scope["refrncr.request_id"])

    @app.get("/readyz", include_in_schema=False)
    async def ready(request: Request):
        try:
            available = readiness_engine is not None and await readiness_status(readiness_engine)
        except Exception:  # noqa: BLE001 - readiness is deliberately fail-closed.
            available = False
        if available:
            return operational_response("ok", request.scope["refrncr.request_id"])
        logging.getLogger(LOGGER_NAME).warning(
            "Readiness unavailable",
            extra={
                "event": "health.readiness_failed",
                "request_id": request.scope["refrncr.request_id"],
                "http_method": "GET",
                "route": "/readyz",
                "status_code": 503,
                "error_category": "database_unavailable",
            },
        )
        return operational_response(
            "unavailable", request.scope["refrncr.request_id"], status_code=503
        )

    flask_app = create_flask_app(
        services,
        allow_loopback_http=allow_loopback_http and not trusted_proxies,
        source_resolver=SourceIdentityResolver.from_config(trusted_proxies),
        clock=clock,
    )
    app.mount("/", WSGIMiddleware(flask_app))
    return app

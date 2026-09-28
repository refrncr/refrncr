import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from a2wsgi import WSGIMiddleware
from fastapi import FastAPI, Request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from refrncr.api.documentation import publish_documentation
from refrncr.api.routes import create_api
from refrncr.api_keys import ApiKeyService, configured_api_keys
from refrncr.audit_history import AuditHistoryService
from refrncr.dataset_versions import DatasetVersionService
from refrncr.hierarchy import HierarchyService
from refrncr.login_security import SourceIdentityResolver
from refrncr.operational import (
    LOGGER_NAME,
    OperationalMiddleware,
    operational_response,
    readiness_status,
)
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
)
from refrncr.web import create_flask_app


@dataclass(frozen=True)
class CommunityServices:
    api_keys: ApiKeyService | None
    dataset_versions: DatasetVersionService
    audit_history: AuditHistoryService
    users: UserService
    authentication: AuthenticationService
    sessions: SessionService
    teams: TeamService
    memberships: MembershipService
    grants: GrantService
    authorization: AuthorizationService
    datasets: DatasetService
    dataset_fields: DatasetFieldService
    dataset_business_keys: DatasetBusinessKeyService
    dataset_records: DatasetRecordService
    record_imports: DatasetRecordImportService
    hierarchy: HierarchyService


def create_community_services(
    sessions: sessionmaker[Session], resources: ResourceTypeRegistry
) -> CommunityServices:
    """Compose shared services with the Community SQLAlchemy adapters."""
    uow = SqlAlchemyUnitOfWorkFactory(sessions)
    permission_data = SqlAlchemyPermissionDataSource(sessions)
    resources.register("dataset", DatasetResourceResolver(uow))
    authorization = AuthorizationService(
        permission_data, resources, DatasetAuthorizationService(uow)
    )
    versions = DatasetVersionService(uow, authorization.datasets)
    dataset_records = DatasetRecordService(uow, authorization, versions=versions)
    return CommunityServices(
        api_keys=configured_api_keys(uow),
        dataset_versions=versions,
        audit_history=AuditHistoryService(uow),
        users=UserService(uow),
        authentication=AuthenticationService(uow),
        sessions=SessionService(sessions),
        teams=TeamService(uow),
        memberships=MembershipService(uow),
        grants=GrantService(uow, resources),
        authorization=authorization,
        datasets=DatasetService(uow, authorization, versions=versions),
        dataset_fields=DatasetFieldService(uow, authorization, versions=versions),
        dataset_business_keys=DatasetBusinessKeyService(uow, authorization, versions=versions),
        dataset_records=dataset_records,
        record_imports=DatasetRecordImportService(dataset_records),
        hierarchy=HierarchyService(uow, authorization, versions=versions),
    )


def create_application(
    users: UserService,
    authentication: AuthenticationService,
    sessions: SessionService,
    teams: TeamService,
    memberships: MembershipService,
    *,
    authorization: AuthorizationService,
    datasets: DatasetService,
    dataset_fields: DatasetFieldService,
    dataset_business_keys: DatasetBusinessKeyService,
    dataset_records: DatasetRecordService | None = None,
    record_imports: DatasetRecordImportService | None = None,
    allow_loopback_http: bool = False,
    trusted_proxies: tuple[str, ...] = (),
    audit_history: AuditHistoryService | None = None,
    dataset_versions: DatasetVersionService | None = None,
    api_keys: ApiKeyService | None = None,
    readiness_engine: Engine | None = None,
) -> FastAPI:
    users.bootstrap_organisation()

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
    versions = (
        dataset_versions
        or datasets.versions
        or DatasetVersionService(datasets.uow, authorization.datasets)
    )
    api = create_api(versions, api_keys)
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
        except Exception:
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
        users,
        authentication,
        sessions,
        teams,
        memberships,
        authorization=authorization,
        datasets=datasets,
        dataset_fields=dataset_fields,
        dataset_business_keys=dataset_business_keys,
        dataset_records=dataset_records,
        record_imports=record_imports,
        allow_loopback_http=allow_loopback_http and not trusted_proxies,
        source_resolver=SourceIdentityResolver.from_config(trusted_proxies),
        audit_history=audit_history,
        api_keys=api_keys,
        dataset_versions=dataset_versions,
    )
    app.mount("/", WSGIMiddleware(flask_app))
    return app

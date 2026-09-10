from dataclasses import dataclass

from a2wsgi import WSGIMiddleware
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from refrncr.audit_history import AuditHistoryService
from refrncr.login_security import SourceIdentityResolver
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
    dataset_records = DatasetRecordService(uow, authorization)
    return CommunityServices(
        audit_history=AuditHistoryService(uow),
        users=UserService(uow),
        authentication=AuthenticationService(uow),
        sessions=SessionService(sessions),
        teams=TeamService(uow),
        memberships=MembershipService(uow),
        grants=GrantService(uow, resources),
        authorization=authorization,
        datasets=DatasetService(uow, authorization),
        dataset_fields=DatasetFieldService(uow, authorization),
        dataset_business_keys=DatasetBusinessKeyService(uow, authorization),
        dataset_records=dataset_records,
        record_imports=DatasetRecordImportService(dataset_records),
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
) -> FastAPI:
    users.bootstrap_organisation()
    app = FastAPI(title="refrncr")

    @app.get("/api/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
    )
    app.mount("/", WSGIMiddleware(flask_app))
    return app

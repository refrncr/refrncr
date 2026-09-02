from dataclasses import dataclass

from a2wsgi import WSGIMiddleware
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from refrncr.login_security import SourceIdentityResolver
from refrncr.persistence import SqlAlchemyPermissionDataSource, SqlAlchemyUnitOfWorkFactory
from refrncr.services import (
    AuthenticationService,
    AuthorizationService,
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
    users: UserService
    authentication: AuthenticationService
    sessions: SessionService
    teams: TeamService
    memberships: MembershipService
    grants: GrantService
    authorization: AuthorizationService
    datasets: DatasetService


def create_community_services(
    sessions: sessionmaker[Session], resources: ResourceTypeRegistry
) -> CommunityServices:
    """Compose shared services with the Community SQLAlchemy adapters."""
    uow = SqlAlchemyUnitOfWorkFactory(sessions)
    permission_data = SqlAlchemyPermissionDataSource(sessions)
    resources.register("dataset", DatasetResourceResolver(uow))
    return CommunityServices(
        users=UserService(uow),
        authentication=AuthenticationService(uow),
        sessions=SessionService(sessions),
        teams=TeamService(uow),
        memberships=MembershipService(uow),
        grants=GrantService(uow, resources),
        authorization=AuthorizationService(permission_data, resources),
        datasets=DatasetService(uow),
    )


def create_application(
    users: UserService,
    authentication: AuthenticationService,
    sessions: SessionService,
    teams: TeamService,
    memberships: MembershipService,
    *,
    allow_loopback_http: bool = False,
    trusted_proxies: tuple[str, ...] = (),
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
        allow_loopback_http=allow_loopback_http and not trusted_proxies,
        source_resolver=SourceIdentityResolver.from_config(trusted_proxies),
    )
    app.mount("/", WSGIMiddleware(flask_app))
    return app

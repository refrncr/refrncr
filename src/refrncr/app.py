from dataclasses import dataclass

from a2wsgi import WSGIMiddleware
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from refrncr.persistence import SqlAlchemyPermissionDataSource, SqlAlchemyUnitOfWorkFactory
from refrncr.services import (
    AuthorizationService,
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
    sessions: SessionService
    teams: TeamService
    memberships: MembershipService
    grants: GrantService
    authorization: AuthorizationService


def create_community_services(
    sessions: sessionmaker[Session], resources: ResourceTypeRegistry
) -> CommunityServices:
    """Compose shared services with the Community SQLAlchemy adapters."""
    uow = SqlAlchemyUnitOfWorkFactory(sessions)
    permission_data = SqlAlchemyPermissionDataSource(sessions)
    return CommunityServices(
        users=UserService(uow),
        sessions=SessionService(sessions),
        teams=TeamService(uow),
        memberships=MembershipService(uow),
        grants=GrantService(uow, resources),
        authorization=AuthorizationService(permission_data, resources),
    )


def create_application(
    users: UserService,
    sessions: SessionService,
    teams: TeamService,
    memberships: MembershipService,
) -> FastAPI:
    users.bootstrap_organisation()
    app = FastAPI(title="refrncr")

    @app.get("/api/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    flask_app = create_flask_app(users, sessions, teams, memberships)
    app.mount("/", WSGIMiddleware(flask_app))
    return app

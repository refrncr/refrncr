import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, Grant, Membership, Team, User
from refrncr.persistence import SqlAlchemyPermissionDataSource, SqlAlchemyUnitOfWorkFactory
from refrncr.services import (
    GrantService,
    MembershipService,
    ResourceTypeRegistry,
    TeamService,
    UserService,
)


@pytest.fixture
def persistence_setup():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    uow = SqlAlchemyUnitOfWorkFactory(sessions)
    users = UserService(uow)
    teams = TeamService(uow)
    memberships = MembershipService(uow)
    resources = ResourceTypeRegistry()
    resources.register("document", lambda resource_id: resource_id in {"one", "two"})
    grants = GrantService(uow, resources)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "valid password")
    user = users.create_user(admin.id, "user@example.com", "User", "valid password")
    return sessions, uow, users, teams, memberships, grants, admin, user


def test_permission_data_source_returns_current_direct_and_team_grants(persistence_setup):
    sessions, _uow, _users, teams, memberships, grants, admin, user = persistence_setup
    team = teams.create_team(admin.id, "Editors")
    membership = memberships.create_membership(admin.id, user.id, team.id)
    grants.create_grant(admin.id, "user", user.id, "document", "one", "viewer")
    grants.create_grant(admin.id, "team", team.id, "document", "one", "editor")
    grants.create_grant(admin.id, "team", team.id, "document", "two", "admin")
    source = SqlAlchemyPermissionDataSource(sessions)

    context = source.load_context(user.id, "document", "one")
    assert set(context.permissions) == {"viewer", "editor"}

    memberships.delete_membership(admin.id, team.id, membership.id)
    context = source.load_context(user.id, "document", "one")
    assert context.permissions == ("viewer",)


def test_unit_of_work_operation_groups_and_audit_share_one_transaction(persistence_setup):
    sessions, uow_factory, _users, _teams, _memberships, _grants, admin, user = persistence_setup

    with uow_factory(write=True) as uow:
        team = uow.teams.create(name="Atomic")
        uow.flush()
        membership = uow.memberships.create(user_id=user.id, team_id=team.id)
        uow.flush()
        uow.add_audit(
            actor_user_id=admin.id,
            object_type="membership",
            object_id=membership.id,
            action="created",
        )

    with sessions() as session:
        assert session.get(Team, team.id) is not None
        assert session.get(Membership, membership.id) is not None
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_id == membership.id)
            )
            == 1
        )


def test_unit_of_work_rolls_back_cleanup_when_audit_commit_fails(persistence_setup):
    sessions, uow_factory, _users, teams, memberships, grants, admin, user = persistence_setup
    team = teams.create_team(admin.id, "Keep")
    membership = memberships.create_membership(admin.id, user.id, team.id)
    grant = grants.create_grant(admin.id, "team", team.id, "document", "one", "viewer")

    with pytest.raises(IntegrityError), uow_factory(write=True) as uow:
        stored_team = uow.teams.get(team.id, for_update=True)
        for stored_grant in uow.grants.list_for_team(team.id, for_update=True):
            uow.grants.delete(stored_grant)
        uow.teams.delete(stored_team)
        uow.add_audit(
            actor_user_id=str(uuid.uuid4()),
            object_type="team",
            object_id=team.id,
            action="deleted",
        )

    with sessions() as session:
        assert session.get(Team, team.id) is not None
        assert session.get(Membership, membership.id) is not None
        assert session.get(Grant, grant.id) is not None
        assert session.get(User, admin.id) is not None

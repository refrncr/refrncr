import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.persistence import SqlAlchemyPermissionDataSource, SqlAlchemyUnitOfWorkFactory
from refrncr.services import (
    AuthorizationError,
    AuthorizationService,
    GrantService,
    MembershipService,
    NotFoundError,
    ResourceTypeRegistry,
    TeamService,
    UserService,
    ValidationError,
)


class PermissionScenario:
    def __init__(self, factory):
        self.factory = factory
        uow = SqlAlchemyUnitOfWorkFactory(factory)
        self.users = UserService(uow)
        self.teams = TeamService(uow)
        self.memberships = MembershipService(uow)
        self.resources = ResourceTypeRegistry()
        self.resources.register("test-resource", {"resource-1", "resource-2"}.__contains__)
        self.resources.register("other-resource", {"resource-1"}.__contains__)
        self.grants = GrantService(uow, self.resources)
        self.authorization = AuthorizationService(
            SqlAlchemyPermissionDataSource(factory), self.resources
        )
        self.users.bootstrap_organisation()
        self.admin = self.users.create_initial_admin("admin@example.com", "Admin", "valid password")
        self.user = self.users.create_user(
            self.admin.id, "user@example.com", "User", "valid password"
        )

    def create_team_source(self, name: str, permission: str, *, user_id: str | None = None):
        team = self.teams.create_team(self.admin.id, name)
        membership = self.memberships.create_membership(
            self.admin.id, user_id or self.user.id, team.id
        )
        grant = self.grants.create_grant(
            self.admin.id,
            "team",
            team.id,
            "test-resource",
            "resource-1",
            permission,
        )
        return team, membership, grant

    def create_direct_grant(self, permission: str, *, user_id: str | None = None):
        return self.grants.create_grant(
            self.admin.id,
            "user",
            user_id or self.user.id,
            "test-resource",
            "resource-1",
            permission,
        )

    def effective(self, *, user_id: str | None = None) -> str | None:
        return self.authorization.effective_permission(
            user_id or self.user.id, "test-resource", "resource-1"
        )


@pytest.fixture
def scenario():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return PermissionScenario(create_session_factory(engine))


def test_highest_permission_wins_across_multiple_team_memberships(scenario):
    scenario.create_team_source("Viewers", "viewer")
    scenario.create_team_source("Editors", "editor")

    assert scenario.effective() == "editor"


@pytest.mark.parametrize(
    "sources",
    [
        [("Viewers", "viewer"), ("Editors", "editor"), ("Admins", "admin")],
        [("Admins", "admin"), ("Viewers", "viewer"), ("Editors", "editor")],
        [("Editors", "editor"), ("Admins", "admin"), ("Viewers", "viewer")],
    ],
)
def test_three_team_result_is_independent_of_creation_order(scenario, sources):
    for team_name, permission in sources:
        scenario.create_team_source(team_name, permission)

    assert scenario.effective() == "admin"


@pytest.mark.parametrize("permission", ["viewer", "editor"])
def test_equal_team_permissions_are_not_elevated(scenario, permission):
    for index in range(3):
        scenario.create_team_source(f"Team {index}", permission)

    assert scenario.effective() == permission
    required = "editor" if permission == "viewer" else "admin"
    assert not scenario.authorization.check_access(
        scenario.user.id, "test-resource", "resource-1", required
    )


@pytest.mark.parametrize(
    ("direct_permission", "team_permission", "expected"),
    [
        ("viewer", "editor", "editor"),
        ("admin", "viewer", "admin"),
        ("editor", "editor", "editor"),
    ],
)
def test_direct_and_team_grants_use_highest_permission(
    scenario, direct_permission, team_permission, expected
):
    scenario.create_direct_grant(direct_permission)
    scenario.create_team_source("Inherited", team_permission)

    assert scenario.effective() == expected


def test_removing_highest_membership_falls_back_to_next_source(scenario):
    scenario.create_direct_grant("viewer")
    scenario.create_team_source("Editors", "editor")
    team, membership, _grant = scenario.create_team_source("Admins", "admin")
    assert scenario.effective() == "admin"

    scenario.memberships.delete_membership(scenario.admin.id, team.id, membership.id)

    assert scenario.effective() == "editor"


def test_removing_lower_membership_retains_higher_permission(scenario):
    team, membership, _grant = scenario.create_team_source("Viewers", "viewer")
    scenario.create_team_source("Admins", "admin")

    scenario.memberships.delete_membership(scenario.admin.id, team.id, membership.id)

    assert scenario.effective() == "admin"


def test_removing_highest_team_grant_falls_back(scenario):
    scenario.create_direct_grant("viewer")
    scenario.create_team_source("Editors", "editor")
    _team, _membership, grant = scenario.create_team_source("Admins", "admin")
    assert scenario.effective() == "admin"

    scenario.grants.delete_grant(scenario.admin.id, grant.id)

    assert scenario.effective() == "editor"


def test_removing_direct_grant_retains_team_permission(scenario):
    direct = scenario.create_direct_grant("admin")
    scenario.create_team_source("Editors", "editor")

    scenario.grants.delete_grant(scenario.admin.id, direct.id)

    assert scenario.effective() == "editor"


def test_removing_one_equal_source_retains_permission(scenario):
    team, membership, _grant = scenario.create_team_source("Editors A", "editor")
    scenario.create_team_source("Editors B", "editor")

    scenario.memberships.delete_membership(scenario.admin.id, team.id, membership.id)

    assert scenario.effective() == "editor"


def test_removing_final_source_restores_default_deny(scenario):
    direct = scenario.create_direct_grant("viewer")
    assert scenario.effective() == "viewer"

    scenario.grants.delete_grant(scenario.admin.id, direct.id)

    assert scenario.effective() is None
    assert not scenario.authorization.check_access(
        scenario.user.id, "test-resource", "resource-1", "viewer"
    )


def test_memberships_without_applicable_grants_are_default_deny(scenario):
    team = scenario.teams.create_team(scenario.admin.id, "No access")
    scenario.memberships.create_membership(scenario.admin.id, scenario.user.id, team.id)

    assert scenario.effective() is None
    assert not scenario.authorization.check_access(
        scenario.user.id, "test-resource", "resource-1", "viewer"
    )


def test_grants_for_other_resource_id_or_type_do_not_apply(scenario):
    team = scenario.teams.create_team(scenario.admin.id, "Other resources")
    scenario.memberships.create_membership(scenario.admin.id, scenario.user.id, team.id)
    scenario.grants.create_grant(
        scenario.admin.id,
        "team",
        team.id,
        "test-resource",
        "resource-2",
        "admin",
    )
    scenario.grants.create_grant(
        scenario.admin.id,
        "team",
        team.id,
        "other-resource",
        "resource-1",
        "admin",
    )

    assert scenario.effective() is None


def test_team_grant_requires_current_persisted_membership(scenario):
    team = scenario.teams.create_team(scenario.admin.id, "Unjoined")
    scenario.grants.create_grant(
        scenario.admin.id,
        "team",
        team.id,
        "test-resource",
        "resource-1",
        "admin",
    )

    assert scenario.effective() is None

    membership = scenario.memberships.create_membership(
        scenario.admin.id, scenario.user.id, team.id
    )
    assert scenario.effective() == "admin"

    scenario.memberships.delete_membership(scenario.admin.id, team.id, membership.id)
    assert scenario.effective() is None


def test_inactive_and_deleted_users_cannot_inherit_team_permission(scenario):
    disabled = scenario.users.create_user(
        scenario.admin.id, "disabled@example.com", "Disabled", "valid password"
    )
    deleted = scenario.users.create_user(
        scenario.admin.id, "deleted@example.com", "Deleted", "valid password"
    )
    team = scenario.teams.create_team(scenario.admin.id, "Privileged")
    for user in [disabled, deleted]:
        scenario.memberships.create_membership(scenario.admin.id, user.id, team.id)
    scenario.grants.create_grant(
        scenario.admin.id,
        "team",
        team.id,
        "test-resource",
        "resource-1",
        "admin",
    )
    scenario.users.disable_user(scenario.admin.id, disabled.id)
    scenario.users.delete_user(scenario.admin.id, deleted.id)

    for user in [disabled, deleted]:
        assert scenario.effective(user_id=user.id) is None
        assert not scenario.authorization.check_access(
            user.id, "test-resource", "resource-1", "viewer"
        )


def test_missing_and_unregistered_resources_are_rejected(scenario):
    with pytest.raises(NotFoundError):
        scenario.authorization.effective_permission(scenario.user.id, "test-resource", "missing")
    with pytest.raises(ValidationError):
        scenario.authorization.effective_permission(scenario.user.id, "unregistered", "resource-1")


def test_resource_admin_does_not_grant_community_administration(scenario):
    scenario.create_direct_grant("admin")

    assert scenario.effective() == "admin"
    assert scenario.authorization.check_access(
        scenario.user.id, "test-resource", "resource-1", "admin"
    )
    with pytest.raises(AuthorizationError):
        scenario.teams.create_team(scenario.user.id, "Forbidden")


def test_multiple_lower_permissions_never_combine_into_higher_access(scenario):
    scenario.create_direct_grant("viewer")
    for index in range(4):
        scenario.create_team_source(f"Viewer team {index}", "viewer")

    assert scenario.effective() == "viewer"
    assert not scenario.authorization.check_access(
        scenario.user.id, "test-resource", "resource-1", "editor"
    )


def assert_postgresql_multi_source_evaluation(factory):
    scenario = PermissionScenario(factory)
    scenario.create_direct_grant("viewer")
    scenario.create_team_source("Viewers", "viewer")
    scenario.create_team_source("Editors", "editor")
    scenario.create_team_source("Admins", "admin")

    assert scenario.effective() == "admin"


def test_postgresql_multi_team_and_direct_grant_evaluation():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    administration_engine = create_engine(database_url)
    schema = f"effective_permissions_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_database_engine(schema_url)
    try:
        Base.metadata.create_all(engine)
        assert_postgresql_multi_source_evaluation(create_session_factory(engine))
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, Grant, Membership
from refrncr.persistence import SqlAlchemyPermissionDataSource, SqlAlchemyUnitOfWorkFactory
from refrncr.services import (
    AuthorizationError,
    AuthorizationService,
    ConflictError,
    GrantService,
    InvalidLifecycleError,
    MembershipService,
    NotFoundError,
    ResourceTypeRegistry,
    TeamService,
    UserService,
    ValidationError,
)


@pytest.fixture
def grant_services():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    uow = SqlAlchemyUnitOfWorkFactory(factory)
    users = UserService(uow)
    teams = TeamService(uow)
    memberships = MembershipService(uow)
    resources = ResourceTypeRegistry()
    existing_resources = {"resource-1", "resource-2"}
    resources.register("test-resource", existing_resources.__contains__)
    grants = GrantService(uow, resources)
    authorization = AuthorizationService(SqlAlchemyPermissionDataSource(factory), resources)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "password")
    user = users.create_user(admin.id, "user@example.com", "User", "password")
    team = teams.create_team(admin.id, "Platform")
    return {
        "engine": engine,
        "factory": factory,
        "users": users,
        "teams": teams,
        "memberships": memberships,
        "resources": resources,
        "grants": grants,
        "authorization": authorization,
        "admin": admin,
        "user": user,
        "team": team,
    }


def test_create_user_and_team_grants_with_canonical_fields_and_audit(grant_services):
    values = grant_services
    direct = values["grants"].create_grant(
        values["admin"].id,
        "user",
        values["user"].id,
        "test-resource",
        "resource-1",
        "viewer",
    )
    inherited = values["grants"].create_grant(
        values["admin"].id,
        "team",
        values["team"].id,
        "test-resource",
        "resource-1",
        "editor",
    )

    assert uuid.UUID(direct.id)
    assert direct.created_at is not None and direct.updated_at is not None
    assert inherited.subject_type == "team"
    with values["factory"]() as session:
        events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.object_type == "grant",
                    AuditEvent.action == "created",
                )
            )
        )
        assert {event.object_id for event in events} == {direct.id, inherited.id}
        assert {event.actor_user_id for event in events} == {values["admin"].id}


def test_change_and_delete_grant_share_lifecycle_audit(grant_services):
    values = grant_services
    grant = values["grants"].create_grant(
        values["admin"].id,
        "user",
        values["user"].id,
        "test-resource",
        "resource-1",
        "viewer",
    )
    previous_updated_at = grant.updated_at

    changed = values["grants"].change_permission(values["admin"].id, grant.id, "admin")
    assert changed.permission == "admin"
    assert changed.updated_at > previous_updated_at
    values["grants"].delete_grant(values["admin"].id, grant.id)

    with values["factory"]() as session:
        assert session.get(Grant, grant.id) is None
        actions = list(
            session.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.object_type == "grant", AuditEvent.object_id == grant.id)
                .order_by(AuditEvent.timestamp)
            )
        )
        assert actions == ["created", "permission_changed", "deleted"]


@pytest.mark.parametrize("permission", ["Viewer", "owner", "", None])
def test_invalid_permission_is_rejected_without_writes(grant_services, permission):
    values = grant_services
    with pytest.raises(ValidationError):
        values["grants"].create_grant(
            values["admin"].id,
            "user",
            values["user"].id,
            "test-resource",
            "resource-1",
            permission,
        )
    with values["factory"]() as session:
        assert session.scalar(select(func.count(Grant.id))) == 0


def test_invalid_subject_and_resource_references_are_rejected(grant_services):
    values = grant_services
    attempts = [
        (ValidationError, "group", values["user"].id, "test-resource", "resource-1"),
        (NotFoundError, "user", str(uuid.uuid4()), "test-resource", "resource-1"),
        (NotFoundError, "team", str(uuid.uuid4()), "test-resource", "resource-1"),
        (ValidationError, "user", values["user"].id, "unknown", "resource-1"),
        (NotFoundError, "user", values["user"].id, "test-resource", "missing"),
    ]
    for error, subject_type, subject_id, resource_type, resource_id in attempts:
        with pytest.raises(error):
            values["grants"].create_grant(
                values["admin"].id,
                subject_type,
                subject_id,
                resource_type,
                resource_id,
                "viewer",
            )
    with values["factory"]() as session:
        assert session.scalar(select(func.count(Grant.id))) == 0
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "grant")
            )
            == 0
        )


def test_inactive_and_deleted_users_cannot_receive_direct_grants(grant_services):
    values = grant_services
    disabled = values["users"].create_user(
        values["admin"].id, "disabled@example.com", "Disabled", "password"
    )
    deleted = values["users"].create_user(
        values["admin"].id, "deleted@example.com", "Deleted", "password"
    )
    values["users"].disable_user(values["admin"].id, disabled.id)
    values["users"].delete_user(values["admin"].id, deleted.id)
    for user in [disabled, deleted]:
        with pytest.raises(InvalidLifecycleError):
            values["grants"].create_grant(
                values["admin"].id,
                "user",
                user.id,
                "test-resource",
                "resource-1",
                "viewer",
            )


def test_duplicate_is_rejected_by_service_and_database(grant_services):
    values = grant_services
    grant = values["grants"].create_grant(
        values["admin"].id,
        "user",
        values["user"].id,
        "test-resource",
        "resource-1",
        "viewer",
    )
    with pytest.raises(ConflictError):
        values["grants"].create_grant(
            values["admin"].id,
            "user",
            values["user"].id,
            "test-resource",
            "resource-1",
            "editor",
        )
    with pytest.raises(IntegrityError), values["factory"].begin() as session:
        session.add(
            Grant(
                subject_type=grant.subject_type,
                subject_id=grant.subject_id,
                resource_type=grant.resource_type,
                resource_id=grant.resource_id,
                permission="admin",
            )
        )
        session.flush()
    with values["factory"]() as session:
        assert session.scalar(select(func.count(Grant.id))) == 1
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "grant")
            )
            == 1
        )


def test_registry_requires_canonical_unique_types_and_resolver_existence():
    resources = ResourceTypeRegistry()
    resources.register("document", lambda resource_id: resource_id == "known")
    resources.require_existing("document", "known")
    with pytest.raises(ConflictError):
        resources.register("document", lambda _resource_id: True)
    for invalid in ["Document", " document", "", "x" * 101]:
        with pytest.raises(ValidationError):
            resources.register(invalid, lambda _resource_id: True)
    with pytest.raises(ValidationError):
        resources.require_existing("unknown", "known")
    with pytest.raises(NotFoundError):
        resources.require_existing("document", "missing")


def test_effective_permission_combines_direct_and_current_team_grants(grant_services):
    values = grant_services
    second_team = values["teams"].create_team(values["admin"].id, "Editors")
    first_membership = values["memberships"].create_membership(
        values["admin"].id, values["user"].id, values["team"].id
    )
    values["memberships"].create_membership(values["admin"].id, values["user"].id, second_team.id)
    grants = [
        ("team", second_team.id, "editor"),
        ("user", values["user"].id, "viewer"),
        ("team", values["team"].id, "admin"),
    ]
    for subject_type, subject_id, permission in reversed(grants):
        values["grants"].create_grant(
            values["admin"].id,
            subject_type,
            subject_id,
            "test-resource",
            "resource-1",
            permission,
        )
    assert (
        values["authorization"].effective_permission(
            values["user"].id, "test-resource", "resource-1"
        )
        == "admin"
    )
    values["memberships"].delete_membership(
        values["admin"].id, values["team"].id, first_membership.id
    )
    assert (
        values["authorization"].effective_permission(
            values["user"].id, "test-resource", "resource-1"
        )
        == "editor"
    )


def test_deny_by_default_hierarchy_and_admin_override_are_separate(grant_services):
    values = grant_services
    authorization = values["authorization"]
    assert (
        authorization.effective_permission(values["user"].id, "test-resource", "resource-1") is None
    )
    assert not authorization.check_access(
        values["user"].id, "test-resource", "resource-1", "viewer"
    )
    assert (
        authorization.effective_permission(values["admin"].id, "test-resource", "resource-1")
        is None
    )
    assert authorization.check_access(values["admin"].id, "test-resource", "resource-1", "admin")
    values["grants"].create_grant(
        values["admin"].id,
        "user",
        values["user"].id,
        "test-resource",
        "resource-1",
        "editor",
    )
    assert authorization.check_access(values["user"].id, "test-resource", "resource-1", "viewer")
    assert authorization.check_access(values["user"].id, "test-resource", "resource-1", "editor")
    assert not authorization.check_access(values["user"].id, "test-resource", "resource-1", "admin")


def test_inactive_user_has_no_effective_or_overridden_access(grant_services):
    values = grant_services
    values["grants"].create_grant(
        values["admin"].id,
        "user",
        values["user"].id,
        "test-resource",
        "resource-1",
        "admin",
    )
    values["users"].disable_user(values["admin"].id, values["user"].id)
    assert (
        values["authorization"].effective_permission(
            values["user"].id, "test-resource", "resource-1"
        )
        is None
    )
    assert not values["authorization"].check_access(
        values["user"].id, "test-resource", "resource-1", "viewer"
    )


def test_resource_must_still_exist_during_authorization(grant_services):
    values = grant_services
    values["grants"].create_grant(
        values["admin"].id,
        "user",
        values["user"].id,
        "test-resource",
        "resource-1",
        "viewer",
    )
    values["resources"]._resolvers["test-resource"] = lambda _resource_id: False
    with pytest.raises(NotFoundError):
        values["authorization"].effective_permission(
            values["user"].id, "test-resource", "resource-1"
        )
    with pytest.raises(NotFoundError):
        values["authorization"].check_access(
            values["admin"].id, "test-resource", "resource-1", "viewer"
        )


def test_grant_administration_requires_active_community_admin(grant_services):
    values = grant_services
    regular = values["user"]
    disabled_admin = values["users"].create_user(
        values["admin"].id,
        "disabled-admin@example.com",
        "Disabled Admin",
        "password",
        role="admin",
    )
    deleted_admin = values["users"].create_user(
        values["admin"].id,
        "deleted-admin@example.com",
        "Deleted Admin",
        "password",
        role="admin",
    )
    values["users"].disable_user(values["admin"].id, disabled_admin.id)
    values["users"].delete_user(values["admin"].id, deleted_admin.id)
    for actor_id in [regular.id, disabled_admin.id, deleted_admin.id, str(uuid.uuid4())]:
        with pytest.raises(AuthorizationError):
            values["grants"].create_grant(
                actor_id,
                "user",
                regular.id,
                "test-resource",
                "resource-1",
                "viewer",
            )


def test_audit_failure_rolls_back_grant_mutations(grant_services, monkeypatch):
    values = grant_services
    grant = values["grants"].create_grant(
        values["admin"].id,
        "user",
        values["user"].id,
        "test-resource",
        "resource-1",
        "viewer",
    )

    def invalid_audit(_actor_id, grant_id, action):
        return AuditEvent(
            actor_user_id=str(uuid.uuid4()),
            object_type="grant",
            object_id=grant_id,
            action=action,
        )

    monkeypatch.setattr(values["grants"], "_audit", invalid_audit)
    with pytest.raises(IntegrityError):
        values["grants"].create_grant(
            values["admin"].id,
            "user",
            values["user"].id,
            "test-resource",
            "resource-2",
            "editor",
        )
    with pytest.raises(IntegrityError):
        values["grants"].change_permission(values["admin"].id, grant.id, "admin")
    with pytest.raises(IntegrityError):
        values["grants"].delete_grant(values["admin"].id, grant.id)
    with values["factory"]() as session:
        stored = session.get(Grant, grant.id)
        assert stored.permission == "viewer"
        assert (
            session.scalar(
                select(Grant).where(
                    Grant.resource_type == "test-resource", Grant.resource_id == "resource-2"
                )
            )
            is None
        )
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "grant")
            )
            == 1
        )


def test_team_deletion_removes_memberships_and_grants_with_audits(grant_services):
    values = grant_services
    membership = values["memberships"].create_membership(
        values["admin"].id, values["user"].id, values["team"].id
    )
    grant = values["grants"].create_grant(
        values["admin"].id,
        "team",
        values["team"].id,
        "test-resource",
        "resource-1",
        "viewer",
    )
    values["teams"].delete_team(values["admin"].id, values["team"].id)
    with values["factory"]() as session:
        assert session.get(Membership, membership.id) is None
        assert session.get(Grant, grant.id) is None
        grant_actions = list(
            session.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.object_type == "grant", AuditEvent.object_id == grant.id
                )
            )
        )
        assert grant_actions == ["created", "deleted"]


def test_team_deletion_cleanup_is_atomic(grant_services, monkeypatch):
    values = grant_services
    membership = values["memberships"].create_membership(
        values["admin"].id, values["user"].id, values["team"].id
    )
    grant = values["grants"].create_grant(
        values["admin"].id,
        "team",
        values["team"].id,
        "test-resource",
        "resource-1",
        "viewer",
    )

    def invalid_audit(_actor_id, grant_id):
        return AuditEvent(
            actor_user_id=str(uuid.uuid4()),
            object_type="grant",
            object_id=grant_id,
            action="deleted",
        )

    monkeypatch.setattr(values["teams"], "_grant_audit", invalid_audit)
    with pytest.raises(IntegrityError):
        values["teams"].delete_team(values["admin"].id, values["team"].id)
    with values["factory"]() as session:
        assert session.get(Membership, membership.id) is not None
        assert session.get(Grant, grant.id) is not None
        assert session.get(type(values["team"]), values["team"].id) is not None


def test_sqlite_grant_constraints_and_concurrent_uniqueness(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'grants.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    uow = SqlAlchemyUnitOfWorkFactory(factory)
    users = UserService(uow)
    resources = ResourceTypeRegistry()
    resources.register("test-resource", lambda _resource_id: True)
    grants = GrantService(uow, resources)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "password")
    user = users.create_user(admin.id, "user@example.com", "User", "password")
    barrier = Barrier(2)

    def create():
        barrier.wait()
        try:
            return grants.create_grant(
                admin.id, "user", user.id, "test-resource", "resource-1", "viewer"
            )
        except ConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: create(), range(2)))
    assert sum(isinstance(result, Grant) for result in results) == 1
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(
            Grant(
                subject_type="group",
                subject_id=user.id,
                resource_type="test-resource",
                resource_id="resource-2",
                permission="owner",
            )
        )
        session.flush()

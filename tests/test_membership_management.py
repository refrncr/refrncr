import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, Membership, Team, User
from refrncr.persistence import SqlAlchemyUnitOfWorkFactory
from refrncr.services import (
    AuthorizationError,
    ConflictError,
    InvalidLifecycleError,
    MembershipService,
    NotFoundError,
    TeamService,
    UserService,
)


@pytest.fixture
def membership_services():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    uow = SqlAlchemyUnitOfWorkFactory(factory)
    users = UserService(uow)
    teams = TeamService(uow)
    memberships = MembershipService(uow)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "valid password")
    user = users.create_user(admin.id, "user@example.com", "User", "valid password")
    team = teams.create_team(admin.id, "Platform")
    return engine, factory, users, teams, memberships, admin, user, team


def test_admin_creates_memberships_with_stable_ids_and_optional_many_to_many(
    membership_services,
):
    _, factory, users, teams, memberships, admin, user, team = membership_services
    second_team = teams.create_team(admin.id, "Delivery")
    second_user = users.create_user(admin.id, "second@example.com", "Second", "valid password")

    assert memberships.list_team_members(admin.id, team.id) == []
    first = memberships.create_membership(admin.id, user.id, team.id)
    second = memberships.create_membership(admin.id, user.id, second_team.id)
    third = memberships.create_membership(admin.id, second_user.id, team.id)

    assert uuid.UUID(first.id)
    assert first.created_at is not None
    assert {member.id for member, _ in memberships.list_team_members(admin.id, team.id)} == {
        first.id,
        third.id,
    }
    with factory() as session:
        assert session.scalar(select(func.count(Membership.id))) == 3
        assert session.get(Membership, second.id).user_id == user.id


def test_duplicate_membership_is_rejected_without_another_row_or_audit(membership_services):
    _, factory, _, _, memberships, admin, user, team = membership_services
    created = memberships.create_membership(admin.id, user.id, team.id)

    with pytest.raises(ConflictError):
        memberships.create_membership(admin.id, user.id, team.id)

    with factory() as session:
        assert session.scalar(select(func.count(Membership.id))) == 1
        events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.object_type == "membership",
                    AuditEvent.object_id == created.id,
                )
            )
        )
        assert [(event.actor_user_id, event.action) for event in events] == [(admin.id, "created")]


def test_membership_delete_is_physical_and_audited(membership_services):
    _, factory, _, _, memberships, admin, user, team = membership_services
    created = memberships.create_membership(admin.id, user.id, team.id)

    memberships.delete_membership(admin.id, team.id, created.id)

    with factory() as session:
        assert session.get(Membership, created.id) is None
        events = list(
            session.execute(
                select(
                    AuditEvent.actor_user_id,
                    AuditEvent.object_type,
                    AuditEvent.object_id,
                    AuditEvent.action,
                )
                .where(AuditEvent.object_type == "membership")
                .order_by(AuditEvent.timestamp)
            )
        )
        assert events == [
            (admin.id, "membership", created.id, "created"),
            (admin.id, "membership", created.id, "deleted"),
        ]
    with pytest.raises(NotFoundError):
        memberships.delete_membership(admin.id, team.id, created.id)


def test_create_requires_existing_active_user_and_team(membership_services):
    _, factory, users, _, memberships, admin, user, team = membership_services
    disabled = users.create_user(admin.id, "disabled@example.com", "Disabled", "valid password")
    deleted = users.create_user(admin.id, "deleted@example.com", "Deleted", "valid password")
    users.disable_user(admin.id, disabled.id)
    users.delete_user(admin.id, deleted.id)

    with pytest.raises(NotFoundError):
        memberships.create_membership(admin.id, str(uuid.uuid4()), team.id)
    with pytest.raises(NotFoundError):
        memberships.create_membership(admin.id, user.id, str(uuid.uuid4()))
    with pytest.raises(InvalidLifecycleError):
        memberships.create_membership(admin.id, disabled.id, team.id)
    with pytest.raises(InvalidLifecycleError):
        memberships.create_membership(admin.id, deleted.id, team.id)

    with factory() as session:
        assert session.scalar(select(func.count(Membership.id))) == 0
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "membership")
            )
            == 0
        )


def test_disabled_and_deleted_user_memberships_are_retained_and_removable(membership_services):
    _, factory, users, teams, memberships, admin, user, team = membership_services
    disabled_membership = memberships.create_membership(admin.id, user.id, team.id)
    deleted_user = users.create_user(admin.id, "deleted@example.com", "Deleted", "valid password")
    second_team = teams.create_team(admin.id, "Second")
    deleted_membership = memberships.create_membership(admin.id, deleted_user.id, second_team.id)

    users.disable_user(admin.id, user.id)
    users.delete_user(admin.id, deleted_user.id)
    with factory() as session:
        assert session.get(Membership, disabled_membership.id) is not None
        assert session.get(Membership, deleted_membership.id) is not None

    memberships.delete_membership(admin.id, team.id, disabled_membership.id)
    memberships.delete_membership(admin.id, second_team.id, deleted_membership.id)
    with factory() as session:
        assert session.scalar(select(func.count(Membership.id))) == 0


def test_membership_administration_requires_active_admin(membership_services):
    _, _, users, teams, memberships, admin, user, team = membership_services
    regular = users.create_user(admin.id, "regular@example.com", "Regular", "valid password")
    disabled_admin = users.create_user(
        admin.id, "disabled-admin@example.com", "Disabled Admin", "valid password", role="admin"
    )
    deleted_admin = users.create_user(
        admin.id, "deleted-admin@example.com", "Deleted Admin", "valid password", role="admin"
    )
    users.disable_user(admin.id, disabled_admin.id)
    users.delete_user(admin.id, deleted_admin.id)
    existing = memberships.create_membership(admin.id, user.id, team.id)

    for actor_id in [regular.id, disabled_admin.id, deleted_admin.id, str(uuid.uuid4())]:
        with pytest.raises(AuthorizationError):
            memberships.create_membership(actor_id, regular.id, team.id)
        with pytest.raises(AuthorizationError):
            memberships.delete_membership(actor_id, team.id, existing.id)
        with pytest.raises(AuthorizationError):
            memberships.list_team_members(actor_id, team.id)
    assert len(teams.list_teams(admin.id)) == 1


def test_audit_failure_rolls_back_membership_create_and_delete(membership_services, monkeypatch):
    _, factory, _, _, memberships, admin, user, team = membership_services
    existing = memberships.create_membership(admin.id, user.id, team.id)

    def invalid_audit(_actor_id, membership_id, action):
        return AuditEvent(
            actor_user_id=str(uuid.uuid4()),
            object_type="membership",
            object_id=membership_id,
            action=action,
        )

    monkeypatch.setattr(memberships, "_audit", invalid_audit)
    second_user_id = None
    with factory.begin() as session:
        second = User(
            email="second@example.com",
            display_name="Second",
            password_hash="hash",
            role="user",
            is_active=True,
            is_deleted=False,
        )
        session.add(second)
        session.flush()
        second_user_id = second.id
    with pytest.raises(IntegrityError):
        memberships.create_membership(admin.id, second_user_id, team.id)
    with pytest.raises(IntegrityError):
        memberships.delete_membership(admin.id, team.id, existing.id)

    with factory() as session:
        assert session.get(Membership, existing.id) is not None
        assert session.scalar(select(func.count(Membership.id))) == 1
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "membership")
            )
            == 1
        )


def assert_membership_constraints_and_cascade(factory):
    uow = SqlAlchemyUnitOfWorkFactory(factory)
    users = UserService(uow)
    teams = TeamService(uow)
    memberships = MembershipService(uow)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "valid password")
    user = users.create_user(admin.id, "user@example.com", "User", "valid password")
    team = teams.create_team(admin.id, "Platform")
    membership = memberships.create_membership(admin.id, user.id, team.id)

    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(Membership(user_id=user.id, team_id=team.id))
        session.flush()
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(Membership(user_id=str(uuid.uuid4()), team_id=team.id))
        session.flush()
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(Membership(user_id=user.id, team_id=str(uuid.uuid4())))
        session.flush()
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.delete(session.get(User, user.id))
        session.flush()
    with factory() as session:
        assert session.get(User, user.id) is not None
        assert session.get(Membership, membership.id) is not None

    teams.delete_team(admin.id, team.id)
    with factory() as session:
        assert session.get(Membership, membership.id) is None


def test_sqlite_membership_constraints_and_team_cascade(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'memberships.db'}")
    Base.metadata.create_all(engine)
    assert_membership_constraints_and_cascade(create_session_factory(engine))


def assert_membership_concurrency(factory):
    uow = SqlAlchemyUnitOfWorkFactory(factory)
    users = UserService(uow)
    teams = TeamService(uow)
    memberships = MembershipService(uow)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "valid password")
    user = users.create_user(admin.id, "user@example.com", "User", "valid password")
    team = teams.create_team(admin.id, "Platform")
    create_barrier = Barrier(2)

    def create():
        create_barrier.wait()
        try:
            return memberships.create_membership(admin.id, user.id, team.id)
        except ConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        creation_results = list(executor.map(lambda _: create(), range(2)))
    assert sum(isinstance(result, Membership) for result in creation_results) == 1
    assert sum(isinstance(result, ConflictError) for result in creation_results) == 1
    created = next(result for result in creation_results if isinstance(result, Membership))

    delete_barrier = Barrier(2)

    def delete():
        delete_barrier.wait()
        try:
            memberships.delete_membership(admin.id, team.id, created.id)
            return "deleted"
        except NotFoundError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        deletion_results = list(executor.map(lambda _: delete(), range(2)))
    assert deletion_results.count("deleted") == 1
    assert sum(isinstance(result, NotFoundError) for result in deletion_results) == 1
    with factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.object_type == "membership",
                    AuditEvent.object_id == created.id,
                )
            )
        )
        assert [event.action for event in events].count("created") == 1
        assert [event.action for event in events].count("deleted") == 1


def assert_team_membership_races(factory):
    uow = SqlAlchemyUnitOfWorkFactory(factory)
    users = UserService(uow)
    teams = TeamService(uow)
    memberships = MembershipService(uow)
    users.bootstrap_organisation()
    with factory() as session:
        admin = session.scalar(select(User).where(User.email == "admin@example.com"))
    if admin is None:
        admin = users.create_initial_admin("admin@example.com", "Admin", "valid password")
    user = users.create_user(
        admin.id, f"race-{uuid.uuid4().hex}@example.com", "Race User", "valid password"
    )
    create_team = teams.create_team(admin.id, f"Create race {uuid.uuid4().hex}")
    create_barrier = Barrier(2)

    def create_membership():
        create_barrier.wait()
        try:
            return memberships.create_membership(admin.id, user.id, create_team.id)
        except NotFoundError as exc:
            return exc

    def delete_create_team():
        create_barrier.wait()
        teams.delete_team(admin.id, create_team.id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        create_future = executor.submit(create_membership)
        delete_future = executor.submit(delete_create_team)
        create_result = create_future.result()
        delete_future.result()
    assert isinstance(create_result, (Membership, NotFoundError))
    with factory() as session:
        assert session.get(Team, create_team.id) is None
        assert (
            session.scalar(
                select(func.count(Membership.id)).where(Membership.team_id == create_team.id)
            )
            == 0
        )

    delete_team = teams.create_team(admin.id, f"Delete race {uuid.uuid4().hex}")
    membership = memberships.create_membership(admin.id, user.id, delete_team.id)
    delete_barrier = Barrier(2)

    def delete_membership():
        delete_barrier.wait()
        try:
            memberships.delete_membership(admin.id, delete_team.id, membership.id)
            return "deleted"
        except NotFoundError as exc:
            return exc

    def delete_member_team():
        delete_barrier.wait()
        teams.delete_team(admin.id, delete_team.id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        membership_future = executor.submit(delete_membership)
        team_future = executor.submit(delete_member_team)
        membership_result = membership_future.result()
        team_future.result()
    assert membership_result == "deleted" or isinstance(membership_result, NotFoundError)
    with factory() as session:
        assert session.get(Team, delete_team.id) is None
        assert session.get(Membership, membership.id) is None
        deletion_audits = session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.object_type == "membership",
                AuditEvent.object_id == membership.id,
                AuditEvent.action == "deleted",
            )
        )
        assert deletion_audits in {0, 1}


def test_sqlite_membership_concurrency(tmp_path):
    engine = create_database_engine(
        f"sqlite:///{tmp_path / 'concurrent-memberships.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    assert_membership_concurrency(factory)
    assert_team_membership_races(factory)


@pytest.mark.postgres
def test_postgresql_membership_persistence_and_concurrency():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    administration_engine = create_engine(database_url)
    schema = f"membership_service_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_database_engine(schema_url)
    try:
        Base.metadata.create_all(engine)
        factory = create_session_factory(engine)
        assert_membership_constraints_and_cascade(factory)
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        assert_membership_concurrency(factory)
        assert_team_membership_races(factory)
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

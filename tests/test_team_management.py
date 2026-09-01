import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, Team
from refrncr.persistence import SqlAlchemyUnitOfWorkFactory
from refrncr.services import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    TeamService,
    UserService,
    ValidationError,
)


@pytest.fixture
def team_services():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    uow = SqlAlchemyUnitOfWorkFactory(factory)
    users = UserService(uow)
    teams = TeamService(uow)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "password")
    return engine, factory, users, teams, admin


def test_admin_creates_memberless_team_with_normalized_name_and_timestamps(team_services):
    engine, _, _, teams, admin = team_services

    team = teams.create_team(admin.id, "  Cafe\u0301  Platform  ")

    assert uuid.UUID(team.id)
    assert team.name == "Café  Platform"
    assert team.name_key == "café  platform"
    assert team.created_at is not None
    assert team.updated_at is not None
    assert "memberships" in inspect(engine).get_table_names()


def test_model_derives_name_key_and_rejects_independent_public_assignment(team_services):
    factory = team_services[1]

    with pytest.raises(AttributeError):
        Team(name="Rejected", name_key="inconsistent")

    with factory.begin() as session:
        team = Team(name="Alpha", _name_key="inconsistent")
        session.add(team)
    assert team.name_key == "alpha"

    with factory.begin() as session:
        stored = session.get(Team, team.id)
        stored.name = "Beta"
    with factory() as session:
        stored = session.get(Team, team.id)
        assert stored.name == "Beta"
        assert stored.name_key == "beta"


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_team_name_rejects_empty_and_overlong_values(team_services, name):
    factory, teams, admin = team_services[1], team_services[3], team_services[4]

    with pytest.raises(ValidationError):
        teams.create_team(admin.id, name)

    with factory() as session:
        assert session.scalar(select(func.count(Team.id))) == 0
        assert session.scalar(select(func.count(AuditEvent.id))) == 1


def test_team_name_accepts_boundaries_and_preserves_internal_whitespace(team_services):
    _, _, _, teams, admin = team_services

    one_character = teams.create_team(admin.id, " A ")
    one_hundred_characters = teams.create_team(admin.id, "x" * 100)
    spaced = teams.create_team(admin.id, "Alpha   Beta")

    assert one_character.name == "A"
    assert len(one_hundred_characters.name) == 100
    assert spaced.name == "Alpha   Beta"


def test_team_names_are_unique_by_nfc_casefold_key(team_services):
    factory, teams, admin = team_services[1], team_services[3], team_services[4]
    original = teams.create_team(admin.id, "Straße")

    with pytest.raises(ConflictError):
        teams.create_team(admin.id, " STRASSE ")
    composed = teams.create_team(admin.id, "Café")
    with pytest.raises(ConflictError):
        teams.create_team(admin.id, "Cafe\u0301")

    with factory() as session:
        stored = list(session.scalars(select(Team).order_by(Team.name_key)))
        assert {team.id for team in stored} == {original.id, composed.id}
        assert session.scalar(select(func.count(AuditEvent.id))) == 3


def test_rename_updates_team_and_audits_while_conflict_rolls_back(team_services):
    factory, teams, admin = team_services[1], team_services[3], team_services[4]
    first = teams.create_team(admin.id, "First")
    second = teams.create_team(admin.id, "Second")
    previous_updated_at = first.updated_at

    renamed = teams.rename_team(admin.id, first.id, " Renamed ")
    assert renamed.name == "Renamed"
    assert renamed.name_key == "renamed"
    assert renamed.updated_at > previous_updated_at

    with factory() as session:
        second_updated_at = session.get(Team, second.id).updated_at
    with pytest.raises(ConflictError):
        teams.rename_team(admin.id, second.id, "RENAMED")

    with factory() as session:
        stored_second = session.get(Team, second.id)
        assert stored_second.name == "Second"
        assert stored_second.updated_at == second_updated_at
        events = list(
            session.execute(
                select(AuditEvent.action, AuditEvent.actor_user_id)
                .where(AuditEvent.object_type == "team")
                .order_by(AuditEvent.timestamp)
            )
        )
        assert events == [
            ("created", admin.id),
            ("created", admin.id),
            ("renamed", admin.id),
        ]


def test_delete_physically_removes_team_and_audits(team_services):
    factory, teams, admin = team_services[1], team_services[3], team_services[4]
    team = teams.create_team(admin.id, "Temporary")

    teams.delete_team(admin.id, team.id)

    with factory() as session:
        assert session.get(Team, team.id) is None
        events = list(
            session.execute(
                select(AuditEvent.action, AuditEvent.actor_user_id)
                .where(AuditEvent.object_type == "team", AuditEvent.object_id == team.id)
                .order_by(AuditEvent.timestamp)
            )
        )
        assert events == [("created", admin.id), ("deleted", admin.id)]
    with pytest.raises(NotFoundError):
        teams.delete_team(admin.id, team.id)


def test_team_administration_requires_active_administrator(team_services):
    _, _, users, teams, admin = team_services
    existing = teams.create_team(admin.id, "Existing")
    regular = users.create_user(admin.id, "user@example.com", "User", "password")
    disabled_admin = users.create_user(
        admin.id, "disabled@example.com", "Disabled", "password", role="admin"
    )
    deleted_admin = users.create_user(
        admin.id, "deleted@example.com", "Deleted", "password", role="admin"
    )
    users.disable_user(admin.id, disabled_admin.id)
    users.delete_user(admin.id, deleted_admin.id)

    for actor_id in [regular.id, disabled_admin.id, deleted_admin.id, str(uuid.uuid4())]:
        with pytest.raises(AuthorizationError):
            teams.create_team(actor_id, "Forbidden")
        with pytest.raises(AuthorizationError):
            teams.rename_team(actor_id, existing.id, "Forbidden")
        with pytest.raises(AuthorizationError):
            teams.delete_team(actor_id, existing.id)
        with pytest.raises(AuthorizationError):
            teams.list_teams(actor_id)


def test_failed_operations_do_not_create_audit_events(team_services):
    factory, teams, admin = team_services[1], team_services[3], team_services[4]
    team = teams.create_team(admin.id, "Existing")

    with pytest.raises(ConflictError):
        teams.create_team(admin.id, "existing")
    with pytest.raises(ValidationError):
        teams.rename_team(admin.id, team.id, " ")
    with pytest.raises(NotFoundError):
        teams.rename_team(admin.id, str(uuid.uuid4()), "Missing")

    with factory() as session:
        assert session.scalar(select(func.count(AuditEvent.id))) == 2


def test_audit_failure_rolls_back_each_team_operation(team_services, monkeypatch):
    factory, teams, admin = team_services[1], team_services[3], team_services[4]
    rename_target = teams.create_team(admin.id, "Before Rename")
    delete_target = teams.create_team(admin.id, "Keep Me")

    def invalid_audit(_actor_id, team_id, action):
        return AuditEvent(
            actor_user_id=str(uuid.uuid4()),
            object_type="team",
            object_id=team_id,
            action=action,
        )

    monkeypatch.setattr(teams, "_audit", invalid_audit)
    with pytest.raises(IntegrityError):
        teams.create_team(admin.id, "Rolled Back")
    with pytest.raises(IntegrityError):
        teams.rename_team(admin.id, rename_target.id, "After Rename")
    with pytest.raises(IntegrityError):
        teams.delete_team(admin.id, delete_target.id)

    with factory() as session:
        assert session.scalar(select(Team).where(Team.name == "Rolled Back")) is None
        assert session.get(Team, rename_target.id).name == "Before Rename"
        assert session.get(Team, delete_target.id).name == "Keep Me"
        assert session.scalar(select(func.count(AuditEvent.id))) == 3


def test_sqlite_enforces_team_name_constraints(team_services):
    factory = team_services[1]
    with factory.begin() as session:
        session.add(Team(name="Valid", _name_key="ignored"))

    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(Team(name="x" * 101, _name_key="ignored"))
        session.flush()
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(Team(name="VALID", _name_key="different"))
        session.flush()


def assert_concurrent_equivalent_names(factory):
    uow = SqlAlchemyUnitOfWorkFactory(factory)
    users = UserService(uow)
    teams = TeamService(uow)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "password")
    creation_barrier = Barrier(2)

    def create(name):
        creation_barrier.wait()
        try:
            return teams.create_team(admin.id, name)
        except ConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        creation_results = list(executor.map(create, ["Straße", "STRASSE"]))
    assert sum(isinstance(result, Team) for result in creation_results) == 1
    assert sum(isinstance(result, ConflictError) for result in creation_results) == 1

    first = teams.create_team(admin.id, "First")
    second = teams.create_team(admin.id, "Second")
    rename_barrier = Barrier(2)

    def rename(team_id, name):
        rename_barrier.wait()
        try:
            return teams.rename_team(admin.id, team_id, name)
        except ConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        rename_results = [
            executor.submit(rename, first.id, "Shared"),
            executor.submit(rename, second.id, "SHARED"),
        ]
        rename_results = [future.result() for future in rename_results]
    assert sum(isinstance(result, Team) for result in rename_results) == 1
    assert sum(isinstance(result, ConflictError) for result in rename_results) == 1
    with factory() as session:
        assert session.scalar(select(func.count(Team.id)).where(Team.name_key == "shared")) == 1

    with factory.begin() as session:
        directly_persisted = Team(name="Direct", _name_key="inconsistent")
        session.add(directly_persisted)
        session.flush()
        assert directly_persisted.name_key == "direct"
    with factory.begin() as session:
        stored = session.get(Team, directly_persisted.id)
        stored.name = "Changed"
    with factory() as session:
        assert session.get(Team, directly_persisted.id).name_key == "changed"


def test_concurrent_equivalent_names_on_sqlite(tmp_path):
    engine = create_database_engine(
        f"sqlite:///{tmp_path / 'teams.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    assert_concurrent_equivalent_names(create_session_factory(engine))


def test_concurrent_equivalent_names_on_postgresql():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("REFRNCR_TEST_POSTGRES_URL is not configured")
    administration_engine = create_engine(database_url)
    schema = f"team_service_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_database_engine(schema_url)
    try:
        Base.metadata.create_all(engine)
        assert_concurrent_equivalent_names(create_session_factory(engine))
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

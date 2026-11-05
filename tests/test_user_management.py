import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from argon2 import PasswordHasher
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, LoginSession, Organisation, User
from refrncr.persistence import SqlAlchemyUnitOfWorkFactory
from refrncr.services import (
    AuthenticationService,
    AuthorizationError,
    ConflictError,
    InvalidLifecycleError,
    NotFoundError,
    SessionService,
    UserManagementError,
    UserService,
    normalize_email,
)


@pytest.fixture
def services():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    return factory, UserService(SqlAlchemyUnitOfWorkFactory(factory)), SessionService(factory)


def initial_admin(users):
    organisation = users.bootstrap_organisation()
    admin = users.create_initial_admin(
        " Admin@Example.COM ", "Admin", "correct horse battery staple"
    )
    return organisation, admin


def test_single_community_organisation_and_initial_admin(services):
    factory, users, _ = services
    organisation, admin = initial_admin(users)

    assert organisation.id == "community"
    assert users.bootstrap_organisation().id == organisation.id
    assert admin.email == "admin@example.com"
    assert admin.role == "admin"
    assert admin.is_active and not admin.is_deleted
    assert PasswordHasher().verify(admin.password_hash, "correct horse battery staple")
    with factory() as session:
        event = session.scalar(select(AuditEvent))
        assert (event.object_type, event.object_id, event.action, event.actor_user_id) == (
            "user",
            admin.id,
            "created",
            admin.id,
        )


def test_recover_administrator_password_replaces_credential_and_revokes_sessions(services):
    factory, users, sessions = services
    _organisation, admin = initial_admin(users)
    token, _login = sessions.create(admin)

    recovered = users.recover_administrator_password(" ADMIN@example.com ", "replacement password")

    assert recovered.id == admin.id
    assert (
        AuthenticationService(users.uow).authenticate(admin.email, "correct horse battery staple")
        is None
    )
    assert (
        AuthenticationService(users.uow).authenticate(admin.email, "replacement password")
        is not None
    )
    assert sessions.resolve(token) is None
    with factory() as session:
        events = list(session.scalars(select(AuditEvent)))
        assert len(events) == 1
        assert "replacement password" not in repr(events)


def test_recover_administrator_password_rejects_non_admin_and_invalid_password(services):
    _factory, users, _sessions = services
    _organisation, admin = initial_admin(users)
    users.create_user(admin.id, "person@example.com", "Person", "correct horse battery staple")

    with pytest.raises(NotFoundError, match="Administrator not found"):
        users.recover_administrator_password("person@example.com", "replacement password")
    with pytest.raises(NotFoundError, match="Administrator not found"):
        users.recover_administrator_password("other@example.com", "replacement password")
    with pytest.raises(UserManagementError):
        users.recover_administrator_password("admin@example.com", "too short")


def test_database_rejects_a_second_organisation(services):
    factory, users, _ = services
    users.bootstrap_organisation()
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(Organisation(id="another"))


def assert_concurrent_bootstrap_and_initial_admin(factory):
    users = UserService(SqlAlchemyUnitOfWorkFactory(factory))

    bootstrap_barrier = Barrier(2)

    def bootstrap():
        bootstrap_barrier.wait()
        return users.bootstrap_organisation().id

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(lambda _: bootstrap(), range(2))) == [
            "community",
            "community",
        ]

    admin_barrier = Barrier(2)

    def create_admin(index):
        admin_barrier.wait()
        try:
            return users.create_initial_admin(
                f"admin{index}@example.com", f"Admin {index}", "valid password"
            )
        except ConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create_admin, range(2)))

    assert sum(isinstance(result, User) for result in results) == 1
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    with factory() as session:
        assert session.scalar(select(func.count(Organisation.id))) == 1
        assert session.scalar(select(func.count(User.id))) == 1
        assert session.scalar(select(func.count(AuditEvent.id))) == 1

    with factory() as session:
        first_admin = session.scalar(select(User).where(User.is_active.is_(True)))
    second_admin = users.create_user(
        first_admin.id,
        "second-admin@example.com",
        "Second Admin",
        "valid password",
        role="admin",
    )
    removal_barrier = Barrier(2)

    def remove_other_admin(actor_id, target_id):
        removal_barrier.wait()
        try:
            return users.disable_user(actor_id, target_id)
        except (AuthorizationError, InvalidLifecycleError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        removals = [
            executor.submit(remove_other_admin, first_admin.id, second_admin.id),
            executor.submit(remove_other_admin, second_admin.id, first_admin.id),
        ]
        removal_results = [future.result() for future in removals]

    assert sum(isinstance(result, User) for result in removal_results) == 1
    assert sum(isinstance(result, UserManagementError) for result in removal_results) == 1
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(User.id)).where(
                    User.role == "admin",
                    User.is_active.is_(True),
                    User.is_deleted.is_(False),
                )
            )
            == 1
        )


def test_concurrent_bootstrap_and_initial_admin_are_serialized_on_sqlite(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'community.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    assert_concurrent_bootstrap_and_initial_admin(create_session_factory(engine))


@pytest.mark.postgres
def test_concurrent_bootstrap_and_initial_admin_are_serialized_on_postgresql():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    administration_engine = create_engine(database_url)
    schema = f"user_service_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(schema_url)
    try:
        Base.metadata.create_all(engine)
        assert_concurrent_bootstrap_and_initial_admin(create_session_factory(engine))
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_admin_lifecycle_is_audited_and_sessions_are_revoked(services):
    factory, users, sessions = services
    _, admin = initial_admin(users)
    managed = users.create_user(admin.id, "user@example.com", "User", "initial password")
    token, _ = sessions.create(managed)

    users.change_email(admin.id, managed.id, " Changed@Example.com ")
    users.disable_user(admin.id, managed.id)
    assert sessions.resolve(token) is None
    users.enable_user(admin.id, managed.id)
    deletion_token, _ = sessions.create(managed)
    users.delete_user(admin.id, managed.id)
    assert sessions.resolve(deletion_token) is None

    with factory() as session:
        stored = session.get(User, managed.id)
        actions = list(
            session.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.object_type == "user", AuditEvent.object_id == managed.id)
                .order_by(AuditEvent.timestamp)
            )
        )
        assert stored.email == "changed@example.com"
        assert not stored.is_active and stored.is_deleted
        assert actions == ["created", "email_changed", "disabled", "enabled", "deleted"]

    with pytest.raises(InvalidLifecycleError):
        users.enable_user(admin.id, managed.id)


def test_installation_authorization_uniqueness_and_last_admin(services):
    _, users, _ = services
    _, admin = initial_admin(users)
    regular = users.create_user(admin.id, "user@example.com", "User", "valid password")

    with pytest.raises(AuthorizationError):
        users.create_user(regular.id, "other@example.com", "Other", "valid password")
    with pytest.raises(ConflictError):
        users.create_user(admin.id, " USER@example.com ", "Duplicate", "valid password")
    with pytest.raises(InvalidLifecycleError):
        users.disable_user(admin.id, admin.id)
    with pytest.raises(NotFoundError):
        users.disable_user(admin.id, "00000000-0000-0000-0000-000000000000")


def test_audit_schema_accepts_other_object_types_and_actions(services):
    factory, users, _ = services
    _, admin = initial_admin(users)
    with factory.begin() as session:
        session.add(
            AuditEvent(
                actor_user_id=admin.id,
                object_type="dataset",
                object_id="dataset-1",
                action="published",
            )
        )

    with factory() as session:
        event = session.scalar(select(AuditEvent).where(AuditEvent.object_type == "dataset"))
        assert (event.object_id, event.action) == ("dataset-1", "published")


def test_business_operation_rolls_back_when_audit_insert_fails(services, monkeypatch):
    factory, users, _ = services
    _, admin = initial_admin(users)

    def invalid_audit(_actor_id, target, action):
        return AuditEvent(
            actor_user_id="00000000-0000-0000-0000-000000000000",
            object_type="user",
            object_id=target.id,
            action=action,
        )

    monkeypatch.setattr(users, "_audit", invalid_audit)
    with pytest.raises(IntegrityError):
        users.create_user(admin.id, "rolled-back@example.com", "Rolled Back", "valid password")

    with factory() as session:
        assert session.scalar(select(User).where(User.email == "rolled-back@example.com")) is None
        assert session.scalar(select(func.count(AuditEvent.id))) == 1


def test_authentication_and_server_side_session(services):
    _, users, sessions = services
    _, admin = initial_admin(users)
    authentication = AuthenticationService(users.uow)
    assert authentication.authenticate(admin.email, "wrong") is None
    authenticated = authentication.authenticate(admin.email, "correct horse battery staple")
    token, login = sessions.create(authenticated)
    assert inspect(login).detached
    assert login.id is not None
    assert login.csrf_token
    assert token not in login.token_hash
    with services[0]() as session:
        assert session.get(LoginSession, login.id).csrf_token == login.csrf_token
    assert sessions.resolve(token).id == admin.id
    sessions.revoke(token)
    assert sessions.resolve(token) is None


@pytest.mark.parametrize(
    "email",
    ["", "missing-at.example.com", "@example.com", "user@", "a@b@example.com", "a b@example.com"],
)
def test_email_validation_rejects_malformed_addresses(email):
    with pytest.raises(ValueError, match="valid email"):
        normalize_email(email)


def test_email_validation_enforces_database_length():
    with pytest.raises(ValueError, match="valid email"):
        normalize_email(f"{'a' * 309}@example.com")

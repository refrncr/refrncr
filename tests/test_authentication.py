import logging
import os
import unicodedata
import uuid
from dataclasses import FrozenInstanceError

import pytest
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from argon2.low_level import Type
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import User
from refrncr.persistence import SqlAlchemyUnitOfWorkFactory
from refrncr.services import (
    PASSWORD_HASHER,
    AuthenticatedPrincipal,
    AuthenticationService,
    UserService,
    ValidationError,
    validate_password,
)

VALID_PASSWORD = "correct horse battery staple"


class CountingHasher:
    def __init__(self, delegate=PASSWORD_HASHER):
        self.delegate = delegate
        self.verifications = 0
        self.rehash_checks = 0
        self.hashes = 0

    def verify(self, password_hash, password):
        self.verifications += 1
        return self.delegate.verify(password_hash, password)

    def check_needs_rehash(self, password_hash):
        self.rehash_checks += 1
        return self.delegate.check_needs_rehash(password_hash)

    def hash(self, password):
        self.hashes += 1
        return self.delegate.hash(password)


@pytest.fixture
def authentication_services():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    uow = SqlAlchemyUnitOfWorkFactory(sessions)
    users = UserService(uow)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", VALID_PASSWORD)
    return sessions, users, admin, uow


def test_success_returns_small_immutable_principal_not_orm_user(authentication_services):
    _, _, admin, uow = authentication_services

    principal = AuthenticationService(uow).authenticate(admin.email, VALID_PASSWORD)

    assert principal == AuthenticatedPrincipal(id=admin.id)
    assert not isinstance(principal, User)
    with pytest.raises(FrozenInstanceError):
        principal.id = "replacement"


@pytest.mark.parametrize(
    ("case", "email", "password"),
    [
        ("wrong", "admin@example.com", "wrong password"),
        ("malformed", "not-an-email", VALID_PASSWORD),
        ("unknown", "unknown@example.com", VALID_PASSWORD),
        ("inactive", "inactive@example.com", VALID_PASSWORD),
        ("deleted", "deleted@example.com", VALID_PASSWORD),
        ("corrupt", "corrupt@example.com", VALID_PASSWORD),
        ("unsupported", "unsupported@example.com", VALID_PASSWORD),
    ],
)
def test_all_failures_are_generic_and_invoke_verifier(
    authentication_services, case, email, password
):
    sessions, users, admin, uow = authentication_services
    if case in {"inactive", "deleted", "corrupt", "unsupported"}:
        target = users.create_user(admin.id, email, case.title(), VALID_PASSWORD)
        if case == "inactive":
            users.disable_user(admin.id, target.id)
        elif case == "deleted":
            users.delete_user(admin.id, target.id)
        elif case == "corrupt":
            with sessions.begin() as session:
                session.get(User, target.id).password_hash = "not-an-argon2-hash"
        else:
            with sessions.begin() as session:
                session.get(User, target.id).password_hash = "$2b$12$unsupported"
    hasher = CountingHasher()

    result = AuthenticationService(uow, hasher).authenticate(email, password)

    assert result is None
    assert hasher.verifications == (2 if case in {"corrupt", "unsupported"} else 1)
    assert hasher.rehash_checks == 0
    assert hasher.hashes == 0


def test_dummy_hash_is_valid_current_profile_and_performs_full_verification():
    from refrncr.services import _DUMMY_PASSWORD_HASH

    with pytest.raises(VerifyMismatchError):
        PASSWORD_HASHER.verify(_DUMMY_PASSWORD_HASH, "not the dummy password")
    assert not PASSWORD_HASHER.check_needs_rehash(_DUMMY_PASSWORD_HASH)


def test_authentication_does_not_expose_or_log_credentials(authentication_services, caplog):
    _, _, admin, uow = authentication_services
    supplied_password = "never log this password"
    caplog.set_level(logging.DEBUG)

    result = AuthenticationService(uow).authenticate(admin.email, supplied_password)

    assert result is None
    assert supplied_password not in caplog.text
    assert admin.password_hash not in caplog.text


def test_success_rehashes_and_atomically_persists(authentication_services):
    sessions, _, admin, uow = authentication_services
    old_hasher = PasswordHasher(
        time_cost=2,
        memory_cost=32 * 1024,
        parallelism=2,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )
    old_hash = old_hasher.hash(VALID_PASSWORD)
    with sessions.begin() as session:
        session.get(User, admin.id).password_hash = old_hash

    principal = AuthenticationService(uow).authenticate(admin.email, VALID_PASSWORD)

    assert principal == AuthenticatedPrincipal(admin.id)
    with sessions() as session:
        replacement = session.get(User, admin.id).password_hash
    assert replacement != old_hash
    assert not PASSWORD_HASHER.check_needs_rehash(replacement)
    assert PASSWORD_HASHER.verify(replacement, VALID_PASSWORD)


def _prepare_old_hash(sessions, admin):
    old_hasher = PasswordHasher(time_cost=2, memory_cost=32 * 1024, parallelism=2)
    old_hash = old_hasher.hash(VALID_PASSWORD)
    with sessions.begin() as session:
        session.get(User, admin.id).password_hash = old_hash
    return old_hash


class ReplacementHasher(CountingHasher):
    def __init__(self, replacement_hash, on_hash=None):
        super().__init__()
        self.replacement_hash = replacement_hash
        self.on_hash = on_hash

    def hash(self, password):
        self.hashes += 1
        if self.on_hash is not None:
            self.on_hash()
        return self.replacement_hash


def test_compare_and_set_does_not_overwrite_a_concurrent_rehash(authentication_services):
    sessions, _, admin, uow = authentication_services
    old_hash = _prepare_old_hash(sessions, admin)
    concurrent_hash = PASSWORD_HASHER.hash(VALID_PASSWORD)

    def replace_concurrently():
        with sessions.begin() as session:
            session.get(User, admin.id).password_hash = concurrent_hash

    hasher = ReplacementHasher(PASSWORD_HASHER.hash(VALID_PASSWORD), replace_concurrently)

    principal = AuthenticationService(uow, hasher).authenticate(admin.email, VALID_PASSWORD)

    assert principal == AuthenticatedPrincipal(admin.id)
    assert hasher.rehash_checks == 1
    assert hasher.hashes == 1
    with sessions() as session:
        assert session.get(User, admin.id).password_hash == concurrent_hash
        assert session.get(User, admin.id).password_hash != old_hash


@pytest.mark.parametrize("failure_point", ["flush", "commit"])
def test_rehash_persistence_failures_are_generic_and_secret_safe(
    authentication_services, monkeypatch, caplog, failure_point
):
    sessions, _, admin, uow = authentication_services
    old_hash = _prepare_old_hash(sessions, admin)
    replacement_hash = "replacement hash must remain secret"
    hasher = ReplacementHasher(replacement_hash)
    original = getattr(Session, failure_point)

    def fail_persistence(self, *args, **kwargs):
        if self.info.get("password_hash_replacement_attempted"):
            raise OperationalError(
                "secret statement",
                {"old": old_hash, "new": replacement_hash, "password": VALID_PASSWORD},
                Exception("secret database failure"),
            )
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Session, failure_point, fail_persistence)
    caplog.set_level(logging.DEBUG)

    result = AuthenticationService(uow, hasher).authenticate(admin.email, VALID_PASSWORD)

    assert result is None
    assert VALID_PASSWORD not in caplog.text
    assert old_hash not in caplog.text
    assert replacement_hash not in caplog.text
    with sessions() as session:
        assert session.scalar(select(User.password_hash).where(User.id == admin.id)) == old_hash


def test_rehash_does_not_swallow_programming_errors(authentication_services, monkeypatch):
    sessions, _, admin, uow = authentication_services
    _prepare_old_hash(sessions, admin)
    original_flush = Session.flush

    def fail_flush(self, *args, **kwargs):
        if self.info.get("password_hash_replacement_attempted"):
            raise RuntimeError("programming error")
        return original_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", fail_flush)
    with pytest.raises(RuntimeError, match="programming error"):
        AuthenticationService(uow).authenticate(admin.email, VALID_PASSWORD)


@pytest.mark.parametrize("password", ["a" * 12, "é" * 12, " " * 12, "a" * 1024])
def test_password_policy_accepts_boundaries_spaces_and_unicode(password):
    assert validate_password(password) == password


@pytest.mark.parametrize("password", ["a" * 11, "é" * 513, "a" * 1025])
def test_password_policy_rejects_outside_minimum_and_utf8_maximum(password):
    with pytest.raises(ValidationError):
        validate_password(password)


def test_account_creation_preserves_spaces_and_unicode_without_rewriting(
    authentication_services,
):
    _, users, admin, _ = authentication_services
    password = "  cafe\N{COMBINING ACUTE ACCENT} password  "
    normalized = unicodedata.normalize("NFC", password)

    created = users.create_user(admin.id, "unicode@example.com", "Unicode", password)

    assert PASSWORD_HASHER.verify(created.password_hash, password)
    with pytest.raises(VerifyMismatchError):
        PASSWORD_HASHER.verify(created.password_hash, password.strip())
    with pytest.raises(VerifyMismatchError):
        PASSWORD_HASHER.verify(created.password_hash, normalized)


@pytest.fixture
def postgresql_authentication_services():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    administration_engine = create_engine(database_url)
    schema = f"authentication_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(schema_url)
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    uow = SqlAlchemyUnitOfWorkFactory(sessions)
    users = UserService(uow)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", VALID_PASSWORD)
    try:
        yield sessions, admin, uow
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration_engine.dispose()


def test_postgresql_authentication_rehash_and_compare_and_set(
    postgresql_authentication_services,
):
    sessions, admin, uow = postgresql_authentication_services
    authentication = AuthenticationService(uow)

    assert authentication.authenticate(admin.email, VALID_PASSWORD) == AuthenticatedPrincipal(
        admin.id
    )
    assert authentication.authenticate(admin.email, "wrong password") is None

    old_hash = _prepare_old_hash(sessions, admin)
    assert authentication.authenticate(admin.email, VALID_PASSWORD) == AuthenticatedPrincipal(
        admin.id
    )
    with sessions() as session:
        rehashed = session.get(User, admin.id).password_hash
    assert rehashed != old_hash
    assert not PASSWORD_HASHER.check_needs_rehash(rehashed)

    _prepare_old_hash(sessions, admin)
    concurrent_hash = PASSWORD_HASHER.hash(VALID_PASSWORD)

    def replace_concurrently():
        with sessions.begin() as session:
            session.get(User, admin.id).password_hash = concurrent_hash

    racing_hasher = ReplacementHasher(PASSWORD_HASHER.hash(VALID_PASSWORD), replace_concurrently)
    assert AuthenticationService(uow, racing_hasher).authenticate(
        admin.email, VALID_PASSWORD
    ) == AuthenticatedPrincipal(admin.id)
    with sessions() as session:
        assert session.get(User, admin.id).password_hash == concurrent_hash

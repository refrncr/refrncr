import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import LoginSession, User
from refrncr.services import SessionService, UserService


def test_sqlite_database_connection():
    engine = create_database_engine("sqlite:///:memory:")
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        result = session.execute(text("SELECT 1"))

        assert result.scalar_one() == 1


def test_postgresql_database_connection():
    engine = create_database_engine(
        "postgresql+psycopg://refrncr:refrncr@localhost:5432/refrncr_test"
    )
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        result = session.execute(text("SELECT 1"))

        assert result.scalar_one() == 1


def test_sqlite_enforces_session_cascade_and_audit_restriction():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    users = UserService(factory)
    sessions = SessionService(factory)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "password")
    managed = users.create_user(admin.id, "managed@example.com", "Managed", "password")
    _, login = sessions.create(managed)

    with factory.begin() as session:
        session.delete(session.get(User, managed.id))
    with factory() as session:
        assert session.get(LoginSession, login.id) is None

    with pytest.raises(IntegrityError), factory.begin() as session:
        session.delete(session.get(User, admin.id))
        session.flush()
    with factory() as session:
        assert session.scalar(select(User.id).where(User.id == admin.id)) == admin.id

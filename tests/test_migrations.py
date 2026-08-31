import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.services import SessionService, UserService


def assert_community_schema(engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"organisations", "users", "login_sessions", "audit_events"} <= tables
    assert "organisation_id" not in {column["name"] for column in inspector.get_columns("users")}
    assert "organisation_id" not in {
        column["name"] for column in inspector.get_columns("login_sessions")
    }
    audit_columns = {column["name"] for column in inspector.get_columns("audit_events")}
    assert {"object_type", "object_id", "action"} <= audit_columns
    assert "organisation_id" not in audit_columns


def test_empty_sqlite_database_upgrades(tmp_path):
    database = tmp_path / "migration.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")

    engine = create_database_engine(f"sqlite:///{database}")
    assert_community_schema(engine)

    factory = create_session_factory(engine)
    users = UserService(factory)
    sessions = SessionService(factory)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "password")
    managed = users.create_user(admin.id, "managed@example.com", "Managed", "password")
    token, _ = sessions.create(managed)

    assert users.authenticate("managed@example.com", "password").id == managed.id
    assert sessions.resolve(token).id == managed.id


def test_empty_postgresql_database_upgrades():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("REFRNCR_TEST_POSTGRES_URL is not configured")
    administration_engine = create_engine(database_url)
    schema = f"migration_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        config = Config("alembic.ini")
        rendered_url = schema_url.render_as_string(hide_password=False)
        config.set_main_option("sqlalchemy.url", rendered_url.replace("%", "%%"))
        command.upgrade(config, "head")
        assert_community_schema(create_engine(schema_url))
    finally:
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

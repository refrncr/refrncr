import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import Grant, Membership
from refrncr.persistence import SqlAlchemyPermissionDataSource, SqlAlchemyUnitOfWorkFactory
from refrncr.services import (
    AuthorizationService,
    ConflictError,
    GrantService,
    MembershipService,
    ResourceTypeRegistry,
    SessionService,
    TeamService,
    UserService,
)


def assert_community_schema(engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "organisations",
        "users",
        "login_sessions",
        "audit_events",
        "teams",
        "memberships",
        "grants",
    } <= tables
    assert "organisation_id" not in {column["name"] for column in inspector.get_columns("users")}
    assert "organisation_id" not in {
        column["name"] for column in inspector.get_columns("login_sessions")
    }
    audit_columns = {column["name"] for column in inspector.get_columns("audit_events")}
    assert {"object_type", "object_id", "action"} <= audit_columns
    assert "organisation_id" not in audit_columns
    team_columns = {column["name"] for column in inspector.get_columns("teams")}
    assert {"id", "name", "name_key", "created_at", "updated_at"} == team_columns
    assert "organisation_id" not in team_columns
    assert "tenant_id" not in team_columns
    unique_columns = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("teams")
    }
    assert ("name_key",) in unique_columns
    check_names = {constraint["name"] for constraint in inspector.get_check_constraints("teams")}
    assert "ck_teams_name_length" in check_names
    membership_columns = {column["name"] for column in inspector.get_columns("memberships")}
    assert membership_columns == {"id", "user_id", "team_id", "created_at"}
    assert "organisation_id" not in membership_columns
    assert "tenant_id" not in membership_columns
    membership_unique_columns = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("memberships")
    }
    assert ("user_id", "team_id") in membership_unique_columns
    membership_foreign_keys = {
        tuple(foreign_key["constrained_columns"]): foreign_key
        for foreign_key in inspector.get_foreign_keys("memberships")
    }
    assert membership_foreign_keys[("team_id",)]["referred_table"] == "teams"
    assert membership_foreign_keys[("team_id",)]["options"].get("ondelete") == "CASCADE"
    assert membership_foreign_keys[("user_id",)]["referred_table"] == "users"
    assert membership_foreign_keys[("user_id",)]["options"].get("ondelete") in {
        None,
        "RESTRICT",
    }
    grant_columns = {column["name"] for column in inspector.get_columns("grants")}
    assert grant_columns == {
        "id",
        "subject_type",
        "subject_id",
        "resource_type",
        "resource_id",
        "permission",
        "created_at",
        "updated_at",
    }
    assert "organisation_id" not in grant_columns
    assert "tenant_id" not in grant_columns
    grant_unique_columns = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("grants")
    }
    assert ("subject_type", "subject_id", "resource_type", "resource_id") in grant_unique_columns
    assert inspector.get_foreign_keys("grants") == []
    grant_checks = {constraint["name"] for constraint in inspector.get_check_constraints("grants")}
    assert {"ck_grants_subject_type", "ck_grants_permission"} <= grant_checks


def test_empty_sqlite_database_upgrades(tmp_path):
    database = tmp_path / "migration.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")

    engine = create_database_engine(f"sqlite:///{database}")
    assert_community_schema(engine)

    factory = create_session_factory(engine)
    uow = SqlAlchemyUnitOfWorkFactory(factory)
    users = UserService(uow)
    sessions = SessionService(factory)
    teams = TeamService(uow)
    memberships = MembershipService(uow)
    resources = ResourceTypeRegistry()
    resources.register("test-resource", lambda resource_id: resource_id == "resource-1")
    grants = GrantService(uow, resources)
    authorization = AuthorizationService(SqlAlchemyPermissionDataSource(factory), resources)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "password")
    managed = users.create_user(admin.id, "managed@example.com", "Managed", "password")
    token, _ = sessions.create(managed)
    team = teams.create_team(admin.id, "Platform")
    membership = memberships.create_membership(admin.id, managed.id, team.id)
    grant = grants.create_grant(admin.id, "team", team.id, "test-resource", "resource-1", "editor")

    assert users.authenticate("managed@example.com", "password").id == managed.id
    assert sessions.resolve(token).id == managed.id
    assert teams.list_teams(admin.id)[0].id == team.id
    assert authorization.effective_permission(managed.id, "test-resource", "resource-1") == "editor"
    memberships.delete_membership(admin.id, team.id, membership.id)
    assert memberships.list_team_members(admin.id, team.id) == []

    duplicate = memberships.create_membership(admin.id, managed.id, team.id)
    with pytest.raises(ConflictError):
        memberships.create_membership(admin.id, managed.id, team.id)
    teams.delete_team(admin.id, team.id)
    with factory() as session:
        assert session.get(Membership, duplicate.id) is None
        assert session.get(Grant, grant.id) is None

    engine.dispose()
    command.downgrade(config, "0002_ref_34")
    downgraded_engine = create_database_engine(f"sqlite:///{database}")
    assert "memberships" not in inspect(downgraded_engine).get_table_names()
    assert "grants" not in inspect(downgraded_engine).get_table_names()
    assert "teams" in inspect(downgraded_engine).get_table_names()


def test_empty_postgresql_database_upgrades():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("REFRNCR_TEST_POSTGRES_URL is not configured")
    administration_engine = create_engine(database_url)
    schema = f"migration_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    application_engine = None
    try:
        config = Config("alembic.ini")
        rendered_url = schema_url.render_as_string(hide_password=False)
        config.set_main_option("sqlalchemy.url", rendered_url.replace("%", "%%"))
        command.upgrade(config, "head")
        application_engine = create_database_engine(schema_url)
        assert_community_schema(application_engine)
        factory = create_session_factory(application_engine)
        uow = SqlAlchemyUnitOfWorkFactory(factory)
        users = UserService(uow)
        teams = TeamService(uow)
        memberships = MembershipService(uow)
        resources = ResourceTypeRegistry()
        resources.register("test-resource", lambda resource_id: resource_id == "resource-1")
        grants = GrantService(uow, resources)
        authorization = AuthorizationService(SqlAlchemyPermissionDataSource(factory), resources)
        users.bootstrap_organisation()
        admin = users.create_initial_admin("admin@example.com", "Admin", "password")
        managed = users.create_user(admin.id, "managed@example.com", "Managed", "password")
        team = teams.create_team(admin.id, "Straße")
        with pytest.raises(ConflictError):
            teams.create_team(admin.id, "STRASSE")
        membership = memberships.create_membership(admin.id, managed.id, team.id)
        grant = grants.create_grant(
            admin.id, "team", team.id, "test-resource", "resource-1", "admin"
        )
        assert (
            authorization.effective_permission(managed.id, "test-resource", "resource-1") == "admin"
        )
        with pytest.raises(ConflictError):
            memberships.create_membership(admin.id, managed.id, team.id)
        teams.delete_team(admin.id, team.id)
        with factory() as session:
            assert session.get(Membership, membership.id) is None
            assert session.get(Grant, grant.id) is None
        application_engine.dispose()
        application_engine = None
        command.downgrade(config, "0002_ref_34")
        downgraded_engine = create_database_engine(schema_url)
        try:
            assert "memberships" not in inspect(downgraded_engine).get_table_names()
            assert "grants" not in inspect(downgraded_engine).get_table_names()
            assert "teams" in inspect(downgraded_engine).get_table_names()
        finally:
            downgraded_engine.dispose()
    finally:
        if application_engine is not None:
            application_engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

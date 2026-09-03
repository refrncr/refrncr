import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from refrncr.app import create_community_services
from refrncr.cli import prepare_database
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import (
    AuditEvent,
    Dataset,
    DatasetBusinessKeyField,
    DatasetField,
    Grant,
    Membership,
    Team,
    User,
)
from refrncr.persistence import SqlAlchemyPermissionDataSource, SqlAlchemyUnitOfWorkFactory
from refrncr.services import (
    AuthenticationService,
    AuthorizationService,
    ConflictError,
    DatasetResourceResolver,
    DatasetService,
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
        "datasets",
        "dataset_fields",
        "dataset_business_key_fields",
        "dataset_records",
        "dataset_record_values",
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
    dataset_columns = {column["name"] for column in inspector.get_columns("datasets")}
    assert dataset_columns == {
        "id",
        "key",
        "name",
        "description",
        "owner_user_id",
        "created_at",
        "updated_at",
    }
    assert "organisation_id" not in dataset_columns
    assert "tenant_id" not in dataset_columns
    dataset_unique_columns = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("datasets")
    }
    assert ("key",) in dataset_unique_columns
    dataset_checks = {
        constraint["name"] for constraint in inspector.get_check_constraints("datasets")
    }
    assert {
        "ck_datasets_key_length",
        "ck_datasets_name_length",
        "ck_datasets_description_length",
    } <= dataset_checks
    dataset_foreign_keys = {
        tuple(foreign_key["constrained_columns"]): foreign_key
        for foreign_key in inspector.get_foreign_keys("datasets")
    }
    owner_foreign_key = dataset_foreign_keys[("owner_user_id",)]
    assert owner_foreign_key["referred_table"] == "users"
    assert owner_foreign_key["options"].get("ondelete") in {None, "RESTRICT"}
    field_columns = {column["name"]: column for column in inspector.get_columns("dataset_fields")}
    assert set(field_columns) == {
        "id",
        "dataset_id",
        "key",
        "name",
        "data_type",
        "is_required",
        "is_unique",
        "created_at",
        "updated_at",
    }
    assert "organisation_id" not in field_columns
    assert "tenant_id" not in field_columns
    assert "workspace_id" not in field_columns
    for column_name in ("is_required", "is_unique"):
        rendered_default = str(field_columns[column_name]["default"]).strip().casefold()
        assert rendered_default.removesuffix("::boolean").strip("()'\"") in {"0", "false"}
    field_unique_columns = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("dataset_fields")
    }
    assert ("dataset_id", "key") in field_unique_columns
    assert "ix_dataset_fields_dataset_id" not in {
        index["name"] for index in inspector.get_indexes("dataset_fields")
    }
    field_checks = {
        constraint["name"] for constraint in inspector.get_check_constraints("dataset_fields")
    }
    assert {
        "ck_dataset_fields_key_length",
        "ck_dataset_fields_name_length",
        "ck_dataset_fields_data_type",
    } <= field_checks
    field_foreign_keys = {
        tuple(foreign_key["constrained_columns"]): foreign_key
        for foreign_key in inspector.get_foreign_keys("dataset_fields")
    }
    dataset_foreign_key = field_foreign_keys[("dataset_id",)]
    assert dataset_foreign_key["referred_table"] == "datasets"
    assert dataset_foreign_key["options"].get("ondelete") == "CASCADE"
    business_key_columns = {
        column["name"] for column in inspector.get_columns("dataset_business_key_fields")
    }
    assert business_key_columns == {"dataset_id", "field_key"}
    business_key_pk = inspector.get_pk_constraint("dataset_business_key_fields")
    assert business_key_pk["constrained_columns"] == ["dataset_id", "field_key"]
    assert inspector.get_indexes("dataset_business_key_fields") == []
    assert inspector.get_unique_constraints("dataset_business_key_fields") == []
    business_key_foreign_keys = inspector.get_foreign_keys("dataset_business_key_fields")
    assert len(business_key_foreign_keys) == 1
    business_key_foreign_key = business_key_foreign_keys[0]
    assert business_key_foreign_key["constrained_columns"] == ["dataset_id", "field_key"]
    assert business_key_foreign_key["referred_table"] == "dataset_fields"
    assert business_key_foreign_key["referred_columns"] == ["dataset_id", "key"]
    assert business_key_foreign_key["options"].get("ondelete") == "CASCADE"
    record_columns = {column["name"] for column in inspector.get_columns("dataset_records")}
    assert record_columns == {"id", "dataset_id", "identifier_key", "created_at", "updated_at"}
    record_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("dataset_records")
    }
    assert {("id", "dataset_id"), ("dataset_id", "identifier_key")} <= record_uniques
    value_columns = {column["name"] for column in inspector.get_columns("dataset_record_values")}
    assert value_columns == {
        "record_id",
        "field_id",
        "dataset_id",
        "string_value",
        "number_value",
        "boolean_value",
        "date_value",
        "canonical_value_key",
        "unique_value_key",
    }
    value_foreign_keys = {
        tuple(foreign_key["constrained_columns"]): foreign_key
        for foreign_key in inspector.get_foreign_keys("dataset_record_values")
    }
    assert value_foreign_keys[("field_id", "dataset_id")]["options"].get("ondelete") in {
        None,
        "RESTRICT",
    }
    assert value_foreign_keys[("record_id", "dataset_id")]["options"].get("ondelete") == "CASCADE"


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
    authentication = AuthenticationService(uow)
    sessions = SessionService(factory)
    teams = TeamService(uow)
    memberships = MembershipService(uow)
    resources = ResourceTypeRegistry()
    resources.register("test-resource", lambda resource_id: resource_id == "resource-1")
    grants = GrantService(uow, resources)
    authorization = AuthorizationService(SqlAlchemyPermissionDataSource(factory), resources)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "valid password")
    managed = users.create_user(admin.id, "managed@example.com", "Managed", "valid password")
    token, _ = sessions.create(managed)
    team = teams.create_team(admin.id, "Platform")
    membership = memberships.create_membership(admin.id, managed.id, team.id)
    grant = grants.create_grant(admin.id, "team", team.id, "test-resource", "resource-1", "editor")

    assert authentication.authenticate("managed@example.com", "valid password").id == managed.id
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


def test_populated_ref_36_sqlite_database_upgrades_to_ref_72(tmp_path):
    database = tmp_path / "populated-ref-36.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "0004_ref_36")

    engine = create_database_engine(f"sqlite:///{database}")
    factory = create_session_factory(engine)
    uow = SqlAlchemyUnitOfWorkFactory(factory)
    users = UserService(uow)
    teams = TeamService(uow)
    resources = ResourceTypeRegistry()
    resources.register("test-resource", lambda resource_id: resource_id == "resource-1")
    grants = GrantService(uow, resources)
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "valid password")
    team = teams.create_team(admin.id, "Existing team")
    grant = grants.create_grant(admin.id, "team", team.id, "test-resource", "resource-1", "viewer")
    engine.dispose()

    command.upgrade(config, "head")
    upgraded_engine = create_database_engine(f"sqlite:///{database}")
    try:
        assert_community_schema(upgraded_engine)
        with create_session_factory(upgraded_engine)() as session:
            assert session.get(User, admin.id) is not None
            assert session.get(Team, team.id) is not None
            assert session.get(Grant, grant.id) is not None
            assert session.scalar(select(func.count(Dataset.id))) == 0
    finally:
        upgraded_engine.dispose()


def test_populated_ref_72_sqlite_database_upgrades_to_ref_74(tmp_path):
    database = tmp_path / "populated-ref-72.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "0005_ref_72")

    engine = create_database_engine(f"sqlite:///{database}")
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    dataset = services.datasets.create_dataset(admin.id, "existing", "Existing", "Preserved")
    engine.dispose()

    command.upgrade(config, "head")
    upgraded_engine = create_database_engine(f"sqlite:///{database}")
    try:
        assert_community_schema(upgraded_engine)
        with create_session_factory(upgraded_engine)() as session:
            persisted = session.get(Dataset, dataset.id)
            assert (persisted.key, persisted.name, persisted.description) == (
                "existing",
                "Existing",
                "Preserved",
            )
            assert session.scalar(select(func.count(DatasetField.id))) == 0
    finally:
        upgraded_engine.dispose()


def test_populated_ref_74_sqlite_database_upgrades_to_ref_75(tmp_path):
    database = tmp_path / "populated-ref-74.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "0006_ref_74")

    engine = create_database_engine(f"sqlite:///{database}")
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    dataset = services.datasets.create_dataset(admin.id, "existing", "Existing", "Preserved")
    field = services.dataset_fields.create_field(
        admin.id, dataset.key, "code", "Code", "string", is_required=True
    )
    engine.dispose()

    command.upgrade(config, "head")
    upgraded_engine = create_database_engine(f"sqlite:///{database}")
    try:
        assert_community_schema(upgraded_engine)
        with create_session_factory(upgraded_engine)() as session:
            persisted_dataset = session.get(Dataset, dataset.id)
            persisted_field = session.get(DatasetField, field.id)
            assert (persisted_dataset.key, persisted_dataset.description) == (
                "existing",
                "Preserved",
            )
            assert (persisted_field.key, persisted_field.is_required) == ("code", True)
            assert session.scalar(select(func.count(DatasetBusinessKeyField.dataset_id))) == 0
    finally:
        upgraded_engine.dispose()


def test_ref_75_sqlite_composite_fk_duplicate_and_cascade_behavior(tmp_path):
    database = tmp_path / "ref-75-constraints.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine = create_database_engine(f"sqlite:///{database}")
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    first = services.datasets.create_dataset(admin.id, "first", "First", "")
    second = services.datasets.create_dataset(admin.id, "second", "Second", "")
    field = services.dataset_fields.create_field(
        admin.id, first.key, "code", "Code", "string", is_required=True
    )

    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(DatasetBusinessKeyField(dataset_id=second.id, field_key=field.key))
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add_all(
            [
                DatasetBusinessKeyField(dataset_id=first.id, field_key=field.key),
                DatasetBusinessKeyField(dataset_id=first.id, field_key=field.key),
            ]
        )

    with factory.begin() as session:
        session.add(DatasetBusinessKeyField(dataset_id=first.id, field_key=field.key))
    with factory.begin() as session:
        session.delete(session.get(DatasetField, field.id))
    with factory() as session:
        assert session.scalar(select(func.count(DatasetBusinessKeyField.dataset_id))) == 0

    cascading_field = services.dataset_fields.create_field(
        admin.id, first.key, "cascade", "Cascade", "string", is_required=True
    )
    services.dataset_business_keys.set_business_key(admin.id, first.key, (cascading_field.key,))
    with factory.begin() as session:
        session.delete(session.get(Dataset, first.id))
    with factory() as session:
        assert session.get(DatasetField, cascading_field.id) is None
        assert session.scalar(select(func.count(DatasetBusinessKeyField.dataset_id))) == 0


def test_ref_75_sqlite_downgrade_fails_closed_for_business_key_state(tmp_path):
    database = tmp_path / "ref-75-downgrade.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine = create_database_engine(f"sqlite:///{database}")
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    dataset = services.datasets.create_dataset(admin.id, "dataset", "Dataset", "")
    services.dataset_fields.create_field(
        admin.id, dataset.key, "code", "Code", "string", is_required=True
    )
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    engine.dispose()

    with pytest.raises(RuntimeError, match="Dataset business-key rows exist"):
        command.downgrade(config, "0006_ref_74")

    engine = create_database_engine(f"sqlite:///{database}")
    with create_session_factory(engine).begin() as session:
        session.query(DatasetBusinessKeyField).delete()
    engine.dispose()
    command.downgrade(config, "0006_ref_74")
    downgraded_engine = create_database_engine(f"sqlite:///{database}")
    try:
        assert "dataset_business_key_fields" not in inspect(downgraded_engine).get_table_names()
        with create_session_factory(downgraded_engine)() as session:
            assert session.get(Dataset, dataset.id) is not None
    finally:
        downgraded_engine.dispose()


def test_ref_74_sqlite_downgrade_fails_closed_for_field_state(tmp_path):
    database = tmp_path / "ref-74-downgrade.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine = create_database_engine(f"sqlite:///{database}")
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    dataset = services.datasets.create_dataset(admin.id, "dataset", "Dataset", "")
    field = services.dataset_fields.create_field(admin.id, dataset.key, "field", "Field", "string")
    engine.dispose()

    with pytest.raises(RuntimeError, match="Dataset field rows exist"):
        command.downgrade(config, "0005_ref_72")
    engine = create_database_engine(f"sqlite:///{database}")
    with create_session_factory(engine).begin() as session:
        assert session.get(DatasetField, field.id) is not None
        session.delete(session.get(DatasetField, field.id))
    engine.dispose()

    command.downgrade(config, "0005_ref_72")
    downgraded_engine = create_database_engine(f"sqlite:///{database}")
    try:
        assert "dataset_fields" not in inspect(downgraded_engine).get_table_names()
        with create_session_factory(downgraded_engine)() as session:
            assert session.get(Dataset, dataset.id) is not None
    finally:
        downgraded_engine.dispose()


def test_ref_72_sqlite_downgrade_fails_closed_for_dataset_state(tmp_path):
    database = tmp_path / "ref-72-downgrade.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine = create_database_engine(f"sqlite:///{database}")
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    users = services.users
    datasets = services.datasets
    users.bootstrap_organisation()
    admin = users.create_initial_admin("admin@example.com", "Admin", "valid password")
    dataset = datasets.create_dataset(admin.id, "downgrade", "Downgrade", "")
    engine.dispose()

    with pytest.raises(RuntimeError, match="Dataset rows or Dataset grants exist"):
        command.downgrade(config, "0004_ref_36")
    engine = create_database_engine(f"sqlite:///{database}")
    assert "datasets" in inspect(engine).get_table_names()
    with create_session_factory(engine)() as session:
        assert session.get(Dataset, dataset.id) is not None
        assert (
            session.scalar(select(func.count(Grant.id)).where(Grant.resource_type == "dataset"))
            == 1
        )
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "dataset")
            )
            == 1
        )
    with create_session_factory(engine).begin() as session:
        session.delete(session.get(Dataset, dataset.id))
    engine.dispose()

    with pytest.raises(RuntimeError, match="Dataset rows or Dataset grants exist"):
        command.downgrade(config, "0004_ref_36")
    engine = create_database_engine(f"sqlite:///{database}")
    assert "datasets" in inspect(engine).get_table_names()
    with create_session_factory(engine).begin() as session:
        assert session.scalar(select(func.count(Dataset.id))) == 0
        assert (
            session.scalar(select(func.count(Grant.id)).where(Grant.resource_type == "dataset"))
            == 1
        )
        session.execute(Grant.__table__.delete().where(Grant.resource_type == "dataset"))
    engine.dispose()

    command.downgrade(config, "0004_ref_36")
    downgraded_engine = create_database_engine(f"sqlite:///{database}")
    try:
        assert "datasets" not in inspect(downgraded_engine).get_table_names()
        with create_session_factory(downgraded_engine)() as session:
            assert (
                session.scalar(select(func.count(Grant.id)).where(Grant.resource_type == "dataset"))
                == 0
            )
            assert (
                session.scalar(
                    select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "dataset")
                )
                == 1
            )
    finally:
        downgraded_engine.dispose()


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
        rendered_url = schema_url.render_as_string(hide_password=False)
        prepare_database(schema_url)
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", rendered_url.replace("%", "%%"))
        application_engine = create_database_engine(schema_url)
        assert_community_schema(application_engine)
        with application_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == "0008_ref_79"
            )
        factory = create_session_factory(application_engine)
        uow = SqlAlchemyUnitOfWorkFactory(factory)
        users = UserService(uow)
        teams = TeamService(uow)
        memberships = MembershipService(uow)
        resources = ResourceTypeRegistry()
        resources.register("test-resource", lambda resource_id: resource_id == "resource-1")
        resources.register("dataset", DatasetResourceResolver(uow))
        grants = GrantService(uow, resources)
        authorization = AuthorizationService(SqlAlchemyPermissionDataSource(factory), resources)
        datasets = DatasetService(uow, authorization)
        users.bootstrap_organisation()
        admin = users.create_initial_admin("admin@example.com", "Admin", "valid password")
        dataset = datasets.create_dataset(
            admin.id, "alembic-postgresql", "Alembic PostgreSQL", "Migrated schema"
        )
        managed = users.create_user(admin.id, "managed@example.com", "Managed", "valid password")
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
            dataset_grant = session.scalar(
                select(Grant).where(
                    Grant.resource_type == "dataset", Grant.resource_id == dataset.id
                )
            )
            assert session.get(Dataset, dataset.id) is not None
            assert dataset_grant.permission == "admin"
            assert {
                (event.object_type, event.object_id, event.action)
                for event in session.scalars(
                    select(AuditEvent).where(
                        (AuditEvent.object_type == "dataset")
                        | (
                            (AuditEvent.object_type == "grant")
                            & (AuditEvent.object_id == dataset_grant.id)
                        )
                    )
                )
            } == {
                ("dataset", dataset.id, "created"),
                ("grant", dataset_grant.id, "created"),
            }
        application_engine.dispose()
        application_engine = None

        with pytest.raises(RuntimeError, match="Dataset rows or Dataset grants exist"):
            command.downgrade(config, "0004_ref_36")
        application_engine = create_database_engine(schema_url)
        with create_session_factory(application_engine).begin() as session:
            assert session.get(Dataset, dataset.id) is not None
            assert (
                session.scalar(select(func.count(Grant.id)).where(Grant.resource_type == "dataset"))
                == 1
            )
            session.delete(session.get(Dataset, dataset.id))
        application_engine.dispose()
        application_engine = None

        with pytest.raises(RuntimeError, match="Dataset rows or Dataset grants exist"):
            command.downgrade(config, "0004_ref_36")
        application_engine = create_database_engine(schema_url)
        assert "datasets" in inspect(application_engine).get_table_names()
        with create_session_factory(application_engine).begin() as session:
            assert session.scalar(select(func.count(Dataset.id))) == 0
            assert (
                session.scalar(select(func.count(Grant.id)).where(Grant.resource_type == "dataset"))
                == 1
            )
            session.execute(Grant.__table__.delete().where(Grant.resource_type == "dataset"))
        application_engine.dispose()
        application_engine = None

        command.downgrade(config, "0004_ref_36")
        application_engine = create_database_engine(schema_url)
        assert "datasets" not in inspect(application_engine).get_table_names()
        with create_session_factory(application_engine)() as session:
            assert (
                session.scalar(
                    select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "dataset")
                )
                == 1
            )
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

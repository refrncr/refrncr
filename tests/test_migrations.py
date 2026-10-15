import os
import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from dataset_helpers import create_owned_dataset, downgrade_historical_fixture
from sqlalchemy import MetaData, create_engine, func, inspect, select, text
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
    DatasetAuthorizationService,
    DatasetResourceResolver,
    DatasetService,
    GrantService,
    MembershipService,
    ResourceTypeRegistry,
    SessionService,
    TeamService,
    UserService,
)


def assert_community_schema(engine, *, dataset_revision=True, mapping_tables=True):
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
    assert dataset_columns == ({"revision", "icon"} if dataset_revision else set()) | (
        {
            "kind",
            "mapping_mode",
            "source_system",
            "target_system",
            "source_dataset_id",
            "target_dataset_id",
            "source_field_key",
            "target_field_key",
        }
        if mapping_tables
        else set()
    ) | {
        "id",
        "key",
        "name",
        "description",
        "owner_user_id",
        "owner_team_id",
        "content_updated_at",
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
    expected_dataset_checks = {
        "ck_datasets_key_length",
        "ck_datasets_name_length",
        "ck_datasets_description_length",
    }
    if mapping_tables:
        expected_dataset_checks |= {
            "ck_datasets_kind",
            "ck_datasets_mapping_mode",
            "ck_datasets_mapping_metadata",
        }
    assert expected_dataset_checks <= dataset_checks
    dataset_foreign_keys = {
        tuple(foreign_key["constrained_columns"]): foreign_key
        for foreign_key in inspector.get_foreign_keys("datasets")
    }
    owner_foreign_key = dataset_foreign_keys[("owner_user_id",)]
    assert owner_foreign_key["referred_table"] == "users"
    assert owner_foreign_key["options"].get("ondelete") in {None, "RESTRICT"}
    field_columns = {column["name"]: column for column in inspector.get_columns("dataset_fields")}
    assert set(field_columns) == ({"definition", "unit"} if dataset_revision else set()) | (
        {"mapping_role", "source_field_id", "source_field_key", "hierarchy_scope"}
        if mapping_tables
        else set()
    ) | {
        "id",
        "dataset_id",
        "key",
        "name",
        "data_type",
        "is_required",
        "is_unique",
        "display_position",
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
    assert ("dataset_id", "display_position") in field_unique_columns
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
        "ck_dataset_fields_display_position_positive",
    } <= field_checks
    field_foreign_keys = {
        tuple(foreign_key["constrained_columns"]): foreign_key
        for foreign_key in inspector.get_foreign_keys("dataset_fields")
    }
    dataset_foreign_key = field_foreign_keys[("dataset_id",)]
    assert dataset_foreign_key["referred_table"] == "datasets"
    assert dataset_foreign_key["options"].get("ondelete") == "CASCADE"
    if mapping_tables:
        assert field_foreign_keys[("source_field_id",)]["referred_table"] == "dataset_fields"
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
    assert record_columns == ({"deleted_content"} if dataset_revision else set()) | {
        "id",
        "dataset_id",
        "identifier_key",
        "revision",
        "created_at",
        "updated_at",
    }
    revision_column = next(
        column
        for column in inspector.get_columns("dataset_records")
        if column["name"] == "revision"
    )
    assert revision_column["nullable"] is False
    assert revision_column["default"] is None
    record_checks = {
        constraint["name"] for constraint in inspector.get_check_constraints("dataset_records")
    }
    assert "ck_dataset_records_revision_positive" in record_checks
    record_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("dataset_records")
    }
    assert ("id", "dataset_id") in record_uniques
    if dataset_revision:
        identifier_index = next(
            index
            for index in inspector.get_indexes("dataset_records")
            if index["name"] == "uq_dataset_records_dataset_identifier"
        )
        assert identifier_index["unique"]
        assert identifier_index["column_names"] == ["dataset_id", "identifier_key"]
        assert any(
            "deleted_content IS NULL" in str(v)
            for v in identifier_index["dialect_options"].values()
        )
    else:
        assert ("dataset_id", "identifier_key") in record_uniques
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


def test_ref_80_sqlite_migration_backfills_and_downgrades_without_data_loss(tmp_path):
    database = tmp_path / "ref-80.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "0008_ref_79")
    engine = create_database_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, display_name, password_hash, role, is_active, is_deleted, "
                "created_at, updated_at) VALUES "
                "('user-1', 'admin@example.com', 'Admin', 'hash', 'admin', 1, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO datasets "
                "(id, key, name, description, owner_user_id, created_at, updated_at) VALUES "
                "('dataset-1', 'records', 'Records', '', 'user-1', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_records "
                "(id, dataset_id, identifier_key, created_at, updated_at) VALUES "
                "('record-1', 'dataset-1', X'7331', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_fields "
                "(id, dataset_id, key, name, data_type, is_required, is_unique, "
                "created_at, updated_at) VALUES "
                "('field-1', 'dataset-1', 'code', 'Code', 'string', 1, 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_business_key_fields (dataset_id, field_key) "
                "VALUES ('dataset-1', 'code')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_record_values "
                "(record_id, field_id, dataset_id, string_value, canonical_value_key, "
                "unique_value_key) VALUES "
                "('record-1', 'field-1', 'dataset-1', 'one', X'736F6E65', X'736F6E65')"
            )
        )
    engine.dispose()

    command.upgrade(config, "0011_ref_130")
    upgraded = create_database_engine(f"sqlite:///{database}")
    with upgraded.connect() as connection:
        assert (
            connection.scalar(text("SELECT revision FROM dataset_records WHERE id = 'record-1'"))
            == 1
        )
        assert connection.execute(
            text(
                "SELECT string_value, canonical_value_key, unique_value_key "
                "FROM dataset_record_values WHERE record_id = 'record-1'"
            )
        ).one() == ("one", b"sone", b"sone")
    assert (
        next(
            column
            for column in inspect(upgraded).get_columns("dataset_records")
            if column["name"] == "revision"
        )["default"]
        is None
    )
    upgraded.dispose()

    downgrade_historical_fixture(config, "0008_ref_79")
    downgraded = create_database_engine(f"sqlite:///{database}")
    assert "revision" not in {
        column["name"] for column in inspect(downgraded).get_columns("dataset_records")
    }
    with downgraded.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT identifier_key FROM dataset_records WHERE id = 'record-1'")
            )
            == b"s1"
        )
        assert (
            connection.scalar(
                text("SELECT string_value FROM dataset_record_values WHERE record_id = 'record-1'")
            )
            == "one"
        )
    downgraded.dispose()


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
    downgrade_historical_fixture(config, "0002_ref_34")
    downgraded_engine = create_database_engine(f"sqlite:///{database}")
    assert "memberships" not in inspect(downgraded_engine).get_table_names()
    assert "grants" not in inspect(downgraded_engine).get_table_names()
    assert "teams" in inspect(downgraded_engine).get_table_names()


def test_ref_121_sqlite_upgrade_recovers_after_add_column_was_committed(tmp_path):
    database = tmp_path / "partial-ref-121.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "0009_ref_80")
    engine = create_database_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE dataset_fields ADD COLUMN display_position INTEGER")
    engine.dispose()

    command.upgrade(config, "head")

    upgraded = create_database_engine(f"sqlite:///{database}")
    try:
        assert_community_schema(upgraded)
        with upgraded.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0030_ref_205"
            )
    finally:
        upgraded.dispose()


@pytest.mark.parametrize(
    "positions",
    [
        (None, None, None),
        (1, None, 9),
        (1, 1, 1),
        (0, 2, 3),
        (-1, 4, 8),
        (1, 2, 3),
    ],
)
def test_ref_121_partial_values_are_canonically_rebuilt(tmp_path, positions):
    database = tmp_path / "partial-values.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine = create_database_engine(f"sqlite:///{database}")
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    dataset = create_owned_dataset(services.datasets, admin.id, "partial", "Partial", "")
    for key in ("zeta", "alpha", "middle"):
        services.dataset_fields.create_field(
            admin.id,
            dataset.key,
            key,
            key,
            "string",
            is_required=True,
            is_business_key_component=key == "alpha",
        )
    engine.dispose()
    downgrade_historical_fixture(config, "0009_ref_80")
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE dataset_fields ADD COLUMN display_position INTEGER")
        for key, position in zip(("zeta", "alpha", "middle"), positions, strict=True):
            connection.execute(
                text("UPDATE dataset_fields SET display_position=:position WHERE key=:key"),
                {"position": position, "key": key},
            )
    engine.dispose()

    command.upgrade(config, "head")

    upgraded = create_database_engine(f"sqlite:///{database}")
    try:
        with upgraded.connect() as connection:
            assert connection.execute(
                text("SELECT key, display_position FROM dataset_fields ORDER BY display_position")
            ).all() == [("alpha", 1), ("middle", 2), ("zeta", 3)]
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        upgraded.dispose()


def test_prepare_database_rejects_ref_121_revision_with_incomplete_schema(tmp_path):
    database = tmp_path / "false-head.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "0009_ref_80")
    engine = create_database_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='0010_ref_121'"))
    engine.dispose()

    with pytest.raises(RuntimeError, match="schema is incomplete"):
        prepare_database(f"sqlite:///{database}")


def test_sqlite_migration_rejects_external_transaction_without_changing_fk_state(tmp_path):
    database = tmp_path / "external-transaction.db"
    config = Config("alembic.ini")
    engine = create_database_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        transaction = connection.begin()
        config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="ownership of the connection transaction"):
            command.upgrade(config, "head")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        transaction.rollback()
    engine.dispose()


def test_sqlite_migration_failure_restores_foreign_keys_on_supplied_connection(tmp_path):
    database = tmp_path / "leftover-table.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "0009_ref_80")
    engine = create_database_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE _alembic_tmp_dataset_fields (id VARCHAR(36) PRIMARY KEY)"
        )
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="unfinished migration tables"):
            command.upgrade(config, "head")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()


def test_ref_121_sqlite_upgrade_and_downgrade_preserve_relational_snapshot(tmp_path):
    database = tmp_path / "populated-ref-121.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine = create_database_engine(f"sqlite:///{database}")
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    member = services.users.create_user(admin.id, "member@example.com", "Member", "valid password")
    team = services.teams.create_team(admin.id, "Reviewers")
    services.memberships.create_membership(admin.id, member.id, team.id)
    dataset = create_owned_dataset(services.datasets, admin.id, "preserved", "Preserved", "")
    services.grants.create_grant(admin.id, "team", team.id, "dataset", dataset.id, "editor")
    code = services.dataset_fields.create_field(
        admin.id, dataset.key, "code", "Code", "string", is_required=True
    )
    amount = services.dataset_fields.create_field(
        admin.id, dataset.key, "amount", "Amount", "number"
    )
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, (code.key,))
    record = services.dataset_records.create_record(
        admin.id, dataset.key, {code.key: "EUR", amount.key: "12.50"}
    )

    tables = {
        "datasets": "id, key, name, description, owner_user_id",
        "dataset_fields": "id, dataset_id, key, name, data_type, is_required, is_unique",
        "dataset_business_key_fields": "dataset_id, field_key",
        "dataset_records": "id, dataset_id, identifier_key, revision",
        "dataset_record_values": (
            "record_id, field_id, dataset_id, string_value, number_value, boolean_value, "
            "date_value, canonical_value_key, unique_value_key"
        ),
        "memberships": "id, user_id, team_id",
        "grants": "id, subject_type, subject_id, resource_type, resource_id, permission",
        "audit_events": "id, actor_user_id, object_type, object_id, action",
    }

    def snapshot(database_engine):
        with database_engine.connect() as connection:
            return {
                table: connection.exec_driver_sql(
                    f"SELECT {columns} FROM {table} ORDER BY 1, 2"
                ).all()
                for table, columns in tables.items()
            }

    before = snapshot(engine)
    assert before["dataset_records"][0][0] == record.id
    engine.dispose()
    downgrade_historical_fixture(config, "0009_ref_80")
    command.upgrade(config, "head")
    upgraded = create_database_engine(f"sqlite:///{database}")
    assert snapshot(upgraded) == before
    with upgraded.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    upgraded.dispose()
    downgrade_historical_fixture(config, "0009_ref_80")
    downgraded = create_database_engine(f"sqlite:///{database}")
    try:
        assert snapshot(downgraded) == before
        with downgraded.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        downgraded.dispose()


def test_populated_ref_36_sqlite_database_upgrades_to_ref_72(tmp_path):
    database = tmp_path / "populated-ref-36.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "0004_ref_36")

    engine = create_database_engine(f"sqlite:///{database}")
    # Populate the actual historical schema, without assuming current ORM/service
    # columns existed in REF-36 (audit details arrived with REF-73).
    metadata = MetaData()
    metadata.reflect(engine)
    assert "details" not in metadata.tables["audit_events"].c
    admin_id, team_id, grant_id = (str(uuid.uuid4()) for _ in range(3))
    timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            metadata.tables["organisations"].insert(), {"id": "community", "created_at": timestamp}
        )
        connection.execute(
            metadata.tables["users"].insert(),
            dict(
                id=admin_id,
                email="admin@example.com",
                display_name="Admin",
                password_hash="historical-test-only-verifier",
                role="admin",
                is_active=True,
                is_deleted=False,
                created_at=timestamp,
                updated_at=timestamp,
            ),
        )
        connection.execute(
            metadata.tables["teams"].insert(),
            dict(
                id=team_id,
                name="Existing team",
                name_key="existing team",
                created_at=timestamp,
                updated_at=timestamp,
            ),
        )
        connection.execute(
            metadata.tables["grants"].insert(),
            dict(
                id=grant_id,
                subject_type="team",
                subject_id=team_id,
                resource_type="test-resource",
                resource_id="resource-1",
                permission="viewer",
                created_at=timestamp,
                updated_at=timestamp,
            ),
        )
        connection.execute(
            metadata.tables["audit_events"].insert(),
            [
                dict(
                    id=str(uuid.uuid4()),
                    timestamp=timestamp,
                    actor_user_id=admin_id,
                    object_type=kind,
                    object_id=target,
                    action="created",
                )
                for kind, target in (("user", admin_id), ("team", team_id), ("grant", grant_id))
            ],
        )
        historical_events = connection.execute(
            select(metadata.tables["audit_events"]).order_by(metadata.tables["audit_events"].c.id)
        ).all()
    engine.dispose()

    command.upgrade(config, "head")
    upgraded_engine = create_database_engine(f"sqlite:///{database}")
    try:
        assert_community_schema(upgraded_engine)
        with create_session_factory(upgraded_engine)() as session:
            assert session.get(User, admin_id) is not None
            assert session.get(Team, team_id) is not None
            assert session.get(Grant, grant_id) is not None
            assert session.scalar(select(func.count(Dataset.id))) == 0
            events = list(session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
            assert [
                tuple(getattr(event, key) for key in metadata.tables["audit_events"].c.keys())
                for event in events
            ] == historical_events
            assert all(event.details is None for event in events)
    finally:
        upgraded_engine.dispose()


def test_populated_ref_72_sqlite_database_upgrades_to_ref_74(tmp_path):
    database = tmp_path / "populated-ref-72.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")

    engine = create_database_engine(f"sqlite:///{database}")
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    dataset = create_owned_dataset(services.datasets, admin.id, "existing", "Existing", "Preserved")
    engine.dispose()

    downgrade_historical_fixture(config, "0005_ref_72")
    command.upgrade(config, "0014_ref_145")
    upgraded_engine = create_database_engine(f"sqlite:///{database}")
    try:
        assert_community_schema(upgraded_engine, dataset_revision=False, mapping_tables=False)
        with create_session_factory(upgraded_engine)() as session:
            persisted = session.execute(
                select(Dataset.key, Dataset.name, Dataset.description).where(
                    Dataset.id == dataset.id
                )
            ).one()
            assert (persisted.key, persisted.name, persisted.description) == (
                "existing",
                "Existing",
                "Preserved",
            )
            assert session.scalar(select(func.count(DatasetField.id))) == 0
    finally:
        upgraded_engine.dispose()

    from refrncr.version_upgrade import VersionUpgradeError

    with pytest.raises(VersionUpgradeError) as failure:
        command.upgrade(config, "head")
    assert failure.value.invalid == (dataset.id,)


def test_populated_ref_74_sqlite_database_upgrades_to_ref_75(tmp_path):
    database = tmp_path / "populated-ref-74.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")

    engine = create_database_engine(f"sqlite:///{database}")
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    dataset = create_owned_dataset(services.datasets, admin.id, "existing", "Existing", "Preserved")
    field = services.dataset_fields.create_field(
        admin.id, dataset.key, "code", "Code", "string", is_required=True
    )
    engine.dispose()

    downgrade_historical_fixture(config, "0006_ref_74")
    command.upgrade(config, "0014_ref_145")
    upgraded_engine = create_database_engine(f"sqlite:///{database}")
    try:
        assert_community_schema(upgraded_engine, dataset_revision=False, mapping_tables=False)
        with create_session_factory(upgraded_engine)() as session:
            persisted_dataset = session.execute(
                select(Dataset.key, Dataset.name, Dataset.description).where(
                    Dataset.id == dataset.id
                )
            ).one()
            persisted_field = session.execute(
                select(DatasetField.key, DatasetField.is_required).where(
                    DatasetField.id == field.id
                )
            ).one()
            assert (persisted_dataset.key, persisted_dataset.description) == (
                "existing",
                "Preserved",
            )
            assert (persisted_field.key, persisted_field.is_required) == ("code", True)
            assert session.scalar(select(func.count(DatasetBusinessKeyField.dataset_id))) == 0
    finally:
        upgraded_engine.dispose()

    from refrncr.version_upgrade import VersionUpgradeError

    with pytest.raises(VersionUpgradeError) as failure:
        command.upgrade(config, "head")
    assert failure.value.invalid == (dataset.id,)


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
    first = create_owned_dataset(services.datasets, admin.id, "first", "First", "")
    second = create_owned_dataset(services.datasets, admin.id, "second", "Second", "")
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
        session.execute(text("DELETE FROM dataset_fields WHERE id=:id"), {"id": field.id})
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
    dataset = create_owned_dataset(services.datasets, admin.id, "dataset", "Dataset", "")
    services.dataset_fields.create_field(
        admin.id, dataset.key, "code", "Code", "string", is_required=True
    )
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    engine.dispose()

    with pytest.raises(RuntimeError, match="Dataset business-key rows exist"):
        downgrade_historical_fixture(config, "0006_ref_74")

    engine = create_database_engine(f"sqlite:///{database}")
    with create_session_factory(engine).begin() as session:
        session.query(DatasetBusinessKeyField).delete()
    engine.dispose()
    downgrade_historical_fixture(config, "0006_ref_74")
    downgraded_engine = create_database_engine(f"sqlite:///{database}")
    try:
        assert "dataset_business_key_fields" not in inspect(downgraded_engine).get_table_names()
        with create_session_factory(downgraded_engine)() as session:
            assert session.scalar(select(Dataset.id).where(Dataset.id == dataset.id)) == dataset.id
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
    dataset = create_owned_dataset(services.datasets, admin.id, "dataset", "Dataset", "")
    field = services.dataset_fields.create_field(admin.id, dataset.key, "field", "Field", "string")
    engine.dispose()

    with pytest.raises(RuntimeError, match="Dataset field rows exist"):
        downgrade_historical_fixture(config, "0005_ref_72")
    engine = create_database_engine(f"sqlite:///{database}")
    with create_session_factory(engine).begin() as session:
        assert (
            session.scalar(
                text("SELECT count(*) FROM dataset_fields WHERE id=:id"), {"id": field.id}
            )
            == 1
        )
        session.execute(text("DELETE FROM dataset_fields WHERE id=:id"), {"id": field.id})
    engine.dispose()

    downgrade_historical_fixture(config, "0005_ref_72")
    downgraded_engine = create_database_engine(f"sqlite:///{database}")
    try:
        assert "dataset_fields" not in inspect(downgraded_engine).get_table_names()
        with create_session_factory(downgraded_engine)() as session:
            assert session.scalar(select(Dataset.id).where(Dataset.id == dataset.id)) == dataset.id
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
    dataset = create_owned_dataset(datasets, admin.id, "downgrade", "Downgrade", "")
    services.grants.create_grant(admin.id, "user", admin.id, "dataset", dataset.id, "admin")
    engine.dispose()

    with pytest.raises(RuntimeError, match="Dataset rows or Dataset grants exist"):
        downgrade_historical_fixture(config, "0004_ref_36")
    engine = create_database_engine(f"sqlite:///{database}")
    assert "datasets" in inspect(engine).get_table_names()
    with create_session_factory(engine)() as session:
        assert session.scalar(select(Dataset.id).where(Dataset.id == dataset.id)) == dataset.id
        assert (
            session.scalar(select(func.count(Grant.id)).where(Grant.resource_type == "dataset"))
            == 1
        )
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "dataset")
            )
            == 2
        )
    with create_session_factory(engine).begin() as session:
        session.execute(Dataset.__table__.delete().where(Dataset.id == dataset.id))
    engine.dispose()

    with pytest.raises(RuntimeError, match="Dataset rows or Dataset grants exist"):
        downgrade_historical_fixture(config, "0004_ref_36")
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

    downgrade_historical_fixture(config, "0004_ref_36")
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
                == 2
            )
    finally:
        downgraded_engine.dispose()


@pytest.mark.postgres
def test_empty_postgresql_database_upgrades():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
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
                connection.scalar(text("SELECT version_num FROM alembic_version")) == "0030_ref_205"
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
        authorization = AuthorizationService(
            SqlAlchemyPermissionDataSource(factory), resources, DatasetAuthorizationService(uow)
        )
        datasets = DatasetService(uow, authorization)
        users.bootstrap_organisation()
        admin = users.create_initial_admin("admin@example.com", "Admin", "valid password")
        dataset = create_owned_dataset(
            datasets, admin.id, "alembic-postgresql", "Alembic PostgreSQL", "Migrated schema"
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
            assert session.scalar(select(Dataset.id).where(Dataset.id == dataset.id)) == dataset.id
            assert dataset_grant is None
            assert {
                (event.object_type, event.object_id, event.action)
                for event in session.scalars(
                    select(AuditEvent).where(AuditEvent.object_type == "dataset")
                )
            } == {
                ("dataset", dataset.id, "created"),
                ("dataset", dataset.id, "dataset.concept_created"),
            }
        # Historical REF-72 downgrade protection still covers inert legacy Grants.
        with factory.begin() as session:
            session.add(
                Grant(
                    subject_type="user",
                    subject_id=admin.id,
                    resource_type="dataset",
                    resource_id=dataset.id,
                    permission="admin",
                )
            )
        application_engine.dispose()
        application_engine = None

        with pytest.raises(RuntimeError, match="Dataset rows or Dataset grants exist"):
            downgrade_historical_fixture(config, "0004_ref_36")
        application_engine = create_database_engine(schema_url)
        with create_session_factory(application_engine).begin() as session:
            assert session.scalar(select(Dataset.id).where(Dataset.id == dataset.id)) == dataset.id
            assert (
                session.scalar(select(func.count(Grant.id)).where(Grant.resource_type == "dataset"))
                == 1
            )
            session.execute(Dataset.__table__.delete().where(Dataset.id == dataset.id))
        application_engine.dispose()
        application_engine = None

        with pytest.raises(RuntimeError, match="Dataset rows or Dataset grants exist"):
            downgrade_historical_fixture(config, "0004_ref_36")
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

        downgrade_historical_fixture(config, "0004_ref_36")
        application_engine = create_database_engine(schema_url)
        assert "datasets" not in inspect(application_engine).get_table_names()
        with create_session_factory(application_engine)() as session:
            assert (
                session.scalar(
                    select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "dataset")
                )
                == 2
            )
        application_engine.dispose()
        application_engine = None
        downgrade_historical_fixture(config, "0002_ref_34")
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

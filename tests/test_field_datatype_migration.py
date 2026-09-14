"""REF-145 preserves old values/keys and rejects invalid state before DDL."""

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, inspect, select, text
from test_audit_coverage import audit_db as audit_db
from test_dataset_governance_migration import migrate
from test_dataset_records import PASSWORD, configured_dataset

from refrncr.app import create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_session_factory
from refrncr.models import (
    AuditEvent,
    Dataset,
    DatasetBusinessKeyField,
    DatasetField,
    DatasetRecord,
    DatasetRecordValue,
)
from refrncr.services import ResourceTypeRegistry


def old_database(engine):
    Base.metadata.drop_all(engine)
    migrate(engine, "head")
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", PASSWORD)
    dataset, fields = configured_dataset(services, admin)
    services.dataset_records.create_record(
        admin.id,
        dataset.key,
        {
            "code": "Café",
            "amount": "12345678901234567890.123456789012345678",
            "active": "false",
            "day": "2024-02-29",
        },
    )
    # Strip only new lifecycle metadata to construct the historical fixture.
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM dataset_concepts"))
    migrate(engine, "0013_ref_39", downgrade=True)
    return services, admin, dataset, fields


def contents(engine):
    with engine.connect() as connection:
        return {
            model.__tablename__: connection.execute(
                select(
                    *(
                        column
                        for column in model.__table__.columns
                        if not (model is Dataset and column.name in {"revision", "icon"})
                        and not (model is DatasetField and column.name in {"definition", "unit"})
                        and not (model is DatasetRecord and column.name == "deleted_content")
                    )
                )
            ).all()
            for model in (
                Dataset,
                DatasetField,
                DatasetRecord,
                DatasetRecordValue,
                DatasetBusinessKeyField,
                AuditEvent,
            )
        }


def type_constraint(engine):
    return next(
        item["sqltext"]
        for item in inspect(engine).get_check_constraints("dataset_fields")
        if item["name"] == "ck_dataset_fields_data_type"
    )


def test_populated_upgrade_preserves_all_values_identifiers_audits_and_old_ids(audit_db):
    engine, _factory, _services = audit_db
    services, admin, dataset, fields = old_database(engine)
    before = contents(engine)
    migrate(engine, "0014_ref_145")
    assert contents(engine) == before
    assert "timestamp" in type_constraint(engine)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT data_type FROM dataset_fields WHERE dataset_id=:id AND key='amount'"),
                {"id": dataset.id},
            )
            == "number"
        )
        assert connection.scalar(text("SELECT count(*) FROM dataset_record_values")) == 4
    migrate(engine, "0013_ref_39", downgrade=True)
    assert contents(engine) == before
    assert "timestamp" not in type_constraint(engine)
    migrate(engine, "0014_ref_145")
    assert contents(engine) == before


@pytest.mark.parametrize("corruption", ["key", "column", "unique", "unknown_type"])
def test_preflight_failure_leaves_historical_state_and_revision_untouched(audit_db, corruption):
    engine, _factory, _services = audit_db
    _services, _admin, _dataset, fields = old_database(engine)
    if corruption == "unknown_type":
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
            else:
                operations.drop_constraint(
                    "ck_dataset_fields_data_type", "dataset_fields", type_="check"
                )
            connection.execute(
                text("UPDATE dataset_fields SET data_type='unknown' WHERE id=:id"),
                {"id": fields["code"].id},
            )
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
    else:
        with engine.begin() as connection:
            field = fields["code"]
            assignments = {
                "key": {"canonical_value_key": b"s noncanonical "},
                "column": {"string_value": "different"},
                "unique": {"unique_value_key": b"sincorrect"},
            }[corruption]
            connection.execute(
                DatasetRecordValue.__table__.update()
                .where(DatasetRecordValue.field_id == field.id)
                .values(**assignments)
            )
    before = contents(engine)
    with pytest.raises(RuntimeError, match="REF-145"):
        migrate(engine, "0014_ref_145")
    assert contents(engine) == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0013_ref_39"
        if engine.dialect.name == "sqlite":
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()


def test_new_type_downgrade_fails_without_rewriting_or_discarding_data(audit_db):
    engine, _factory, _services = audit_db
    services, admin, dataset, _fields = old_database(engine)
    migrate(engine, "0014_ref_145")
    from datetime import UTC, datetime
    from uuid import uuid4

    with engine.begin() as connection:
        connection.execute(
            DatasetField.__table__.insert().values(
                id=str(uuid4()),
                dataset_id=dataset.id,
                key="instant",
                name="Instant",
                data_type="timestamp",
                is_required=False,
                is_unique=False,
                display_position=len(_fields) + 1,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    before = contents(engine)
    with pytest.raises(RuntimeError, match="unsupported Field types"):
        migrate(engine, "0013_ref_39", downgrade=True)
    assert contents(engine) == before
    assert "timestamp" in type_constraint(engine)


def test_ddl_failure_rolls_back_constraint_and_version(audit_db):
    engine, _factory, _services = audit_db
    old_database(engine)
    before, constraint = contents(engine), type_constraint(engine)

    def fail(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            "CREATE TABLE _alembic_tmp_dataset_fields" in statement
            or "ADD CONSTRAINT ck_dataset_fields_data_type" in statement
        ):
            raise RuntimeError("injected datatype DDL failure")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected datatype DDL failure"):
            migrate(engine, "0014_ref_145")
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert contents(engine) == before
    assert type_constraint(engine) == constraint
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0013_ref_39"


@pytest.mark.parametrize(
    "revision",
    [
        "0001_ref_33",
        "0002_ref_34",
        "0003_ref_35",
        "0004_ref_36",
        "0005_ref_72",
        "0006_ref_74",
        "0007_ref_75",
        "0008_ref_79",
        "0009_ref_80",
        "0010_ref_121",
        "0011_ref_130",
        "0012_ref_73",
        "0013_ref_39",
    ],
)
def test_every_historical_empty_schema_upgrades_to_exact_nine_types(audit_db, revision):
    engine, _factory, _services = audit_db
    Base.metadata.drop_all(engine)
    migrate(engine, revision)
    migrate(engine, "0014_ref_145")
    constraint = type_constraint(engine)
    for type_id in (
        "string",
        "integer",
        "number",
        "float",
        "boolean",
        "date",
        "time",
        "datetime",
        "timestamp",
    ):
        assert f"'{type_id}'" in constraint
    assert "'decimal'" not in constraint
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0014_ref_145"
        assert connection.scalar(text("SELECT count(*) FROM dataset_record_values")) == 0

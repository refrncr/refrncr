"""REF-177 mapping-table migration coverage on configured database engines."""

from alembic import command
from sqlalchemy import inspect, text
from support import config_for


def test_mapping_table_columns_upgrade_and_downgrade(database_audit_db):
    engine, _, _ = database_audit_db
    with engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        command.downgrade(config, "0023_ref_176")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0023_ref_176"
        assert "kind" not in {
            column["name"] for column in inspect(connection).get_columns("datasets")
        }
        connection.commit()
        command.upgrade(config, "head")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0032_ref_239"
        columns = {column["name"] for column in inspect(connection).get_columns("datasets")}
        assert {
            "kind",
            "mapping_mode",
            "source_system",
            "target_system",
            "source_dataset_id",
            "target_dataset_id",
            "source_field_key",
            "target_field_key",
        } <= columns


def test_linked_mapping_fields_upgrade_an_existing_ref_177_database(database_audit_db):
    engine, _, _ = database_audit_db
    with engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        command.downgrade(config, "0024_ref_177")
        columns = {column["name"] for column in inspect(connection).get_columns("datasets")}
        assert "source_field_key" not in columns
        assert "target_field_key" not in columns
        connection.commit()

        command.upgrade(config, "head")

        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0032_ref_239"
        columns = {column["name"] for column in inspect(connection).get_columns("datasets")}
        assert {"source_field_key", "target_field_key"} <= columns


def test_mapping_field_roles_upgrade(database_audit_db):
    engine, _, _ = database_audit_db
    with engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        command.downgrade(config, "0025_ref_177")
        assert "mapping_role" not in {
            column["name"] for column in inspect(connection).get_columns("dataset_fields")
        }
        connection.commit()

        command.upgrade(config, "head")

        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0032_ref_239"
        )
        assert {"mapping_role", "source_field_id", "source_field_key"} <= {
            column["name"] for column in inspect(connection).get_columns("dataset_fields")
        }
        assert {"mapping_role", "source_field_key"} <= {
            column["name"] for column in inspect(connection).get_columns("dataset_version_fields")
        }


def test_mapping_table_migration_preserves_reference_default(database_audit_db):
    engine, _, _ = database_audit_db
    with engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        command.downgrade(config, "0023_ref_176")
        connection.commit()
        command.upgrade(config, "head")
        assert not connection.execute(
            text("SELECT 1 FROM datasets WHERE kind <> 'reference' OR mapping_mode IS NOT NULL")
        ).first()

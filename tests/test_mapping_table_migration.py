"""REF-177 mapping-table migration coverage on configured database engines."""

from alembic import command
from sqlalchemy import inspect, text
from test_audit_coverage import audit_db  # noqa: F401
from test_dataset_version_migration import config_for


def test_mapping_table_columns_upgrade_and_downgrade(audit_db):  # noqa: F811
    engine, _, _ = audit_db
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
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0024_ref_177"
        columns = {column["name"] for column in inspect(connection).get_columns("datasets")}
        assert {
            "kind",
            "mapping_mode",
            "source_system",
            "target_system",
            "source_dataset_id",
            "target_dataset_id",
        } <= columns


def test_mapping_table_migration_preserves_reference_default(audit_db):  # noqa: F811
    engine, _, _ = audit_db
    with engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        command.downgrade(config, "0023_ref_176")
        connection.commit()
        command.upgrade(config, "head")
        assert not connection.execute(
            text("SELECT 1 FROM datasets WHERE kind <> 'reference' OR mapping_mode IS NOT NULL")
        ).first()

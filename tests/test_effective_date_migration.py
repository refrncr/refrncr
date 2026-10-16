"""REF-176 effective-date migration coverage on configured database engines."""

from alembic import command
from sqlalchemy import inspect, text
from support import config_for


def test_effective_date_index_upgrade_and_downgrade(database_audit_db):
    engine, _, _ = database_audit_db
    with engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        command.downgrade(config, "0022_ref_31")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0022_ref_31"
        assert "uq_dataset_versions_effective_date" not in {
            index["name"] for index in inspect(connection).get_indexes("dataset_versions")
        }
        connection.commit()
        command.upgrade(config, "head")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0030_ref_205"
        index = next(
            item
            for item in inspect(connection).get_indexes("dataset_versions")
            if item["name"] == "uq_dataset_versions_effective_date"
        )
        assert index["unique"] and index["column_names"] == ["dataset_id", "effective_date"]

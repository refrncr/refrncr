"""REF-176 effective-date migration coverage on configured database engines."""

from alembic import command
from sqlalchemy import inspect, text
from test_audit_coverage import audit_db  # noqa: F401
from test_dataset_version_migration import config_for


def test_effective_date_index_upgrade_and_downgrade(audit_db):  # noqa: F811
    engine, _, _ = audit_db
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
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0024_ref_177"
        index = next(
            item
            for item in inspect(connection).get_indexes("dataset_versions")
            if item["name"] == "uq_dataset_versions_effective_date"
        )
        assert index["unique"] and index["column_names"] == ["dataset_id", "effective_date"]

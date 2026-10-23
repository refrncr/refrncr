"""REF-179 schema upgrade and destructive-downgrade safeguards on both engines."""

import pytest
from alembic import command
from sqlalchemy import inspect, text
from support import config_for


def test_hierarchy_schema_upgrade_and_downgrade(database_audit_db):
    engine, _, _ = database_audit_db
    with engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        command.downgrade(config, "0027_ref_177")
        assert "dataset_hierarchy_nodes" not in inspect(connection).get_table_names()
        assert "hierarchy_scope" not in {
            column["name"] for column in inspect(connection).get_columns("dataset_fields")
        }
        connection.commit()
        command.upgrade(config, "head")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0032_ref_239"
        assert {
            "dataset_hierarchy_nodes",
            "dataset_hierarchy_values",
            "dataset_version_hierarchy_nodes",
            "dataset_version_hierarchy_values",
        } <= set(inspect(connection).get_table_names())
        for table in ("dataset_fields", "dataset_version_fields"):
            assert "hierarchy_scope" in {
                column["name"] for column in inspect(connection).get_columns(table)
            }
        constraints = {
            item["name"]: item
            for item in inspect(connection).get_foreign_keys("dataset_hierarchy_nodes")
        }
        assert constraints["fk_hierarchy_nodes_parent"]["constrained_columns"] == [
            "parent_id",
            "dataset_id",
        ]


def test_hierarchy_downgrade_refuses_even_empty_hierarchy(database_context):
    with database_context.engine.begin() as connection:
        connection.execute(
            text("UPDATE datasets SET kind = 'hierarchy' WHERE id = :id"),
            {"id": database_context.d.id},
        )
    with database_context.engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        with pytest.raises(RuntimeError, match="Hierarchy data exists"):
            command.downgrade(config, "0027_ref_177")
        connection.rollback()
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0032_ref_239"
        assert "dataset_hierarchy_nodes" in inspect(connection).get_table_names()

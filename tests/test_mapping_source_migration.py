"""REF-239 upgrade preserves references and requires explicit mapping cleanup."""

from uuid import uuid4

import pytest
from alembic import command
from dataset_helpers import create_owned_dataset, publish_dataset
from sqlalchemy import inspect, text
from support import config_for


def test_pinned_source_schema_roundtrip(database_audit_db):
    engine, _, services = database_audit_db
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    source = create_owned_dataset(services.datasets, admin.id, "source", "Source", "")
    publish_dataset(services, admin.id, source)
    with engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        command.downgrade(config, "0030_ref_205")
        before = connection.execute(text("SELECT id, name, number FROM dataset_versions")).all()
        connection.commit()
        command.upgrade(config, "head")
        assert (
            connection.execute(text("SELECT id, name, number FROM dataset_versions")).all()
            == before
        )
        assert "source_version_id" in {
            c["name"] for c in inspect(connection).get_columns("datasets")
        }
        assert any(
            fk["name"] == "fk_versions_source_version"
            for fk in inspect(connection).get_foreign_keys("dataset_versions")
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM dataset_versions WHERE source_version_id IS NOT NULL")
            )
            == 0
        )


@pytest.mark.parametrize("mode", ["standalone", "linked"])
def test_upgrade_refuses_existing_mappings_without_deleting_them(database_audit_db, mode):
    engine, _, services = database_audit_db
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    create_owned_dataset(services.datasets, admin.id, "source", "Source", "")
    with engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        command.downgrade(config, "0030_ref_205")
        connection.execute(
            text("""INSERT INTO datasets
            (id, key, name, description, kind, mapping_mode, owner_user_id,
             owner_team_id, created_at, updated_at, revision)
            SELECT :id, 'old-mapping', 'Old mapping', '', 'mapping', :mode,
                   owner_user_id, owner_team_id, created_at, updated_at, 1
            FROM datasets WHERE key='source'
        """),
            {"id": str(uuid4()), "mode": mode},
        )
        connection.commit()
        with pytest.raises(RuntimeError, match="deleted manually"):
            command.upgrade(config, "head")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0030_ref_205"
        assert connection.scalar(text("SELECT count(*) FROM datasets WHERE kind='mapping'")) == 1
        assert "source_version_id" not in {
            c["name"] for c in inspect(connection).get_columns("datasets")
        }


def test_source_version_requirement_is_service_policy_after_upgrade(database_audit_db):
    from dataset_helpers import create_mapping_dataset
    from sqlalchemy.exc import IntegrityError

    engine, _, services = database_audit_db
    admin = services.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    mapping = create_mapping_dataset(services, admin, "map", "Map", records=("A",))
    with engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "head")
        command.downgrade(config, "0031_ref_239")
        with pytest.raises(IntegrityError):
            connection.execute(
                text("UPDATE datasets SET source_version_id=NULL WHERE id=:id"), {"id": mapping.id}
            )
        connection.rollback()
        command.upgrade(config, "head")
        connection.execute(
            text("UPDATE datasets SET source_version_id=NULL WHERE id=:id"), {"id": mapping.id}
        )
        connection.commit()
        assert (
            connection.scalar(
                text("SELECT source_version_id FROM datasets WHERE id=:id"), {"id": mapping.id}
            )
            is None
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM dataset_records WHERE dataset_id=:id"),
                {"id": mapping.id},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT source_dataset_id FROM datasets WHERE id=:id"), {"id": mapping.id}
            )
            == mapping.source_dataset_id
        )

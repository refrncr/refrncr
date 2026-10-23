"""Real stopped-writer preflight and atomic upgrade from the preceding schema."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text
from support import setup

from refrncr.db.base import Base
from refrncr.models import DatasetVersion
from refrncr.version_upgrade import VersionUpgradeError, publication_preflight


def config_for(connection):
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/refrncr/migrations")
    )
    config.attributes["connection"] = connection
    return config


def previous_schema(c):
    # Construct the real preceding migration schema, preserving populated fixture rows.
    with c.engine.connect() as connection:
        config = config_for(connection)
        connection.execute(text("DELETE FROM dataset_concepts"))
        connection.commit()
        command.stamp(config, "head")
        command.downgrade(config, "0014_ref_145")


@pytest.mark.parametrize("populated", [False, True])
def test_valid_upgrade_and_system_publisher(database_context, populated):
    c = database_context
    setup(c, populated=populated)
    previous_schema(c)
    with c.engine.connect() as connection:
        before = connection.execute(
            text("SELECT name, description, owner_team_id FROM datasets")
        ).all()
        audit = connection.execute(text("SELECT id FROM audit_events")).all()
        connection.commit()
        assert publication_preflight(connection) == ()
        connection.rollback()
        command.upgrade(config_for(connection), "head")
        assert (
            connection.execute(text("SELECT name, description, owner_team_id FROM datasets")).all()
            == before
        )
        assert connection.execute(text("SELECT id FROM audit_events")).all() == audit
    with c.sessions() as session:
        version = session.scalars(select(DatasetVersion)).one()
        assert version.publisher_id is None and version.number == 1
        assert version.record_count == int(populated) and version.field_count == 2
        assert version.effective_date == version.published_at.date()
    with c.engine.connect() as connection:
        command.upgrade(config_for(connection), "head")
        assert connection.scalar(text("SELECT count(*) FROM dataset_versions")) == 1


def test_preflight_all_invalid_and_safe_retry(database_context):
    c = database_context
    c.s.datasets.create_dataset(c.a.id, "second", "Second", "", c.owner.id)
    previous_schema(c)
    with c.engine.connect() as connection:
        expected = tuple(connection.scalars(text("SELECT id FROM datasets ORDER BY id")))
        connection.rollback()
        assert publication_preflight(connection) == expected
        connection.rollback()
        with pytest.raises(VersionUpgradeError) as error:
            command.upgrade(config_for(connection), "head")
        assert error.value.invalid == expected
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0014_ref_145"
        assert "dataset_versions" not in inspect(connection).get_table_names()
        assert "revision" not in {
            col["name"] for col in inspect(connection).get_columns("datasets")
        }
        connection.rollback()
        # Operator corrects old data before retry: valid required identifier fields.
        from uuid import uuid4

        with connection.begin():
            for dataset_id in expected:
                connection.execute(
                    text(
                        "INSERT INTO dataset_fields "
                        "(id,dataset_id,key,name,data_type,is_required,is_unique,"
                        "display_position,created_at,updated_at) "
                        "VALUES (:id,:dataset,'code','Code','string',true,true,1,"
                        "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                    ),
                    {"id": str(uuid4()), "dataset": dataset_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO dataset_business_key_fields (dataset_id,field_key) "
                        "VALUES (:id,'code')"
                    ),
                    {"id": dataset_id},
                )
        command.upgrade(config_for(connection), "head")
        assert connection.scalar(text("SELECT count(*) FROM dataset_versions")) == 2
        if connection.dialect.name == "sqlite":
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()


@pytest.mark.parametrize(
    "revision",
    [
        "base",
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
        "0014_ref_145",
    ],
)
def test_empty_upgrade_from_every_supported_revision(database_audit_db, revision):
    engine, _, _ = database_audit_db
    Base.metadata.drop_all(engine)
    with engine.connect() as connection:
        config = config_for(connection)
        command.upgrade(config, revision)
        command.upgrade(config, "head")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0033_ref_301"
        assert connection.scalar(text("SELECT count(*) FROM dataset_versions")) == 0


@pytest.mark.slow
def test_migration_copy_failure_rolls_back_schema_and_all_versions(database_context, monkeypatch):
    from refrncr.version_persistence import SqlAlchemyVersionOperations

    c = database_context
    setup(c)
    previous_schema(c)
    original = SqlAlchemyVersionOperations.append

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected copy failure")

    with c.engine.connect() as connection:
        with monkeypatch.context() as patch:
            patch.setattr(SqlAlchemyVersionOperations, "append", fail)
            with pytest.raises(RuntimeError, match="injected copy"):
                command.upgrade(config_for(connection), "head")
        assert "dataset_versions" not in inspect(connection).get_table_names()
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0014_ref_145"
        connection.rollback()
        command.upgrade(config_for(connection), "head")
        assert connection.scalar(text("SELECT count(*) FROM dataset_versions")) == 1


@pytest.mark.parametrize("invalid", ["identifier", "required", "datatype", "value"])
def test_invalid_existing_data_preflight(database_context, invalid):
    c = database_context
    setup(c)
    previous_schema(c)
    with c.engine.begin() as connection:
        if invalid == "identifier":
            connection.execute(text("DELETE FROM dataset_business_key_fields"))
        elif invalid == "required":
            connection.execute(text("DELETE FROM dataset_record_values"))
        elif invalid == "datatype":
            # Float is a valid Field type but cannot be an identifier component.
            connection.execute(text("UPDATE dataset_fields SET data_type='float' WHERE key='code'"))
        else:
            connection.execute(
                text("UPDATE dataset_record_values SET canonical_value_key=:bad"),
                {"bad": b"invalid"},
            )
    with c.engine.connect() as connection:
        assert publication_preflight(connection) == (c.d.id,)
        connection.rollback()
        with pytest.raises(VersionUpgradeError):
            command.upgrade(config_for(connection), "head")
        assert "dataset_versions" not in inspect(connection).get_table_names()


def test_populated_downgrade_preserves_versions_and_schema(database_context):
    from support import publish

    c = database_context
    setup(c)
    version = publish(c)
    with c.engine.connect() as connection:
        config = config_for(connection)
        command.stamp(config, "0015_ref_83")
        with pytest.raises(RuntimeError, match="restore the pre-upgrade backup"):
            command.downgrade(config, "0014_ref_145")
        assert connection.scalar(text("SELECT id FROM dataset_versions")) == version.id
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0015_ref_83"
        assert "revision" in {col["name"] for col in inspect(connection).get_columns("datasets")}


@pytest.mark.parametrize("valid", [False, True])
def test_cli_publication_preflight_and_upgrade(database_context, valid, capsys):
    import json

    from refrncr.cli import main

    c = database_context
    if valid:
        setup(c)
    previous_schema(c)
    args = [
        "dataset-publication-preflight",
        "--database-url",
        c.engine.url.render_as_string(hide_password=False),
    ]
    assert main(args) == (0 if valid else 1)
    assert json.loads(capsys.readouterr().out) == {"invalid_dataset_ids": [] if valid else [c.d.id]}
    with c.engine.connect() as connection:
        assert "dataset_versions" not in inspect(connection).get_table_names()
    assert main([*args, "--upgrade"]) == (0 if valid else 1)
    with c.engine.connect() as connection:
        if valid:
            assert connection.scalar(text("SELECT count(*) FROM dataset_versions")) == 1
        else:
            assert "dataset_versions" not in inspect(connection).get_table_names()


def test_migrated_snapshot_constraints_match_current_models(database_context):
    from sqlalchemy import CheckConstraint, UniqueConstraint

    c = database_context
    setup(c)
    previous_schema(c)
    with c.engine.connect() as connection:
        command.upgrade(config_for(connection), "head")
        inspector = inspect(connection)
        for table in Base.metadata.sorted_tables:
            if not table.name.startswith("dataset_version"):
                continue
            assert {item["name"] for item in inspector.get_check_constraints(table.name)} == {
                item.name for item in table.constraints if isinstance(item, CheckConstraint)
            }
            assert {item["name"] for item in inspector.get_unique_constraints(table.name)} == {
                item.name for item in table.constraints if isinstance(item, UniqueConstraint)
            }
            assert {
                tuple(item["constrained_columns"])
                for item in inspector.get_foreign_keys(table.name)
            } == {tuple(item.column_keys) for item in table.foreign_key_constraints}
            assert set(inspector.get_pk_constraint(table.name)["constrained_columns"]) == {
                column.name for column in table.primary_key.columns
            }

"""Real 0015 -> 0016 transitions preserve content and fail before partial migration."""

import pytest
from alembic import command
from sqlalchemy import delete, inspect, select, text, update
from test_audit_coverage import audit_db, context  # noqa: F401
from test_dataset_version_migration import config_for
from test_dataset_versions import publish, setup

from refrncr.concept_upgrade import ConceptUpgradeError, concept_preflight
from refrncr.models import DatasetConcept, DatasetRecordValue


def predecessor(c):
    with c.engine.connect() as connection:
        connection.execute(delete(DatasetConcept))
        connection.commit()
        command.stamp(config_for(connection), "0017_ref_154")
        command.downgrade(config_for(connection), "0015_ref_83")


@pytest.mark.parametrize("state", ["equal", "different", "unpublished"])
def test_lossless_migration_and_retry(context, state):  # noqa: F811
    c = context
    setup(c)
    baseline = publish(c) if state != "unpublished" else None
    if state == "different":
        c.service.create_concept(c.a.id, c.d.id)
        c.s.datasets.update_dataset(c.a.id, c.d.key, "Preserve this draft", "")
    predecessor(c)
    with c.engine.connect() as connection:
        tables = (
            "datasets",
            "dataset_fields",
            "dataset_records",
            "dataset_record_values",
            "dataset_versions",
            "audit_events",
        )
        before = {name: connection.execute(text(f"SELECT * FROM {name}")).all() for name in tables}
        connection.rollback()
        assert concept_preflight(connection) == ()
        connection.rollback()
        command.upgrade(config_for(connection), "head")
        assert {
            name: connection.execute(text(f"SELECT * FROM {name}")).all() for name in tables
        } == before
        rows = connection.execute(select(DatasetConcept.__table__)).mappings().all()
        assert len(rows) == (0 if state == "equal" else 1)
        if rows:
            assert rows[0]["base_version_id"] == (baseline.id if baseline else None)
            assert rows[0]["creator_id"] is None
        connection.commit()
        command.upgrade(config_for(connection), "head")
        assert connection.execute(select(DatasetConcept.__table__)).mappings().all() == rows


def test_corrupt_content_preflight_is_atomic(context):  # noqa: F811
    c = context
    setup(c)
    publish(c)
    predecessor(c)
    with c.engine.begin() as connection:
        connection.execute(update(DatasetRecordValue).values(canonical_value_key=b"bad"))
    with c.engine.connect() as connection:
        assert concept_preflight(connection) == (c.d.id,)
        connection.rollback()
        with pytest.raises(ConceptUpgradeError):
            command.upgrade(config_for(connection), "head")
        assert "dataset_concepts" not in inspect(connection).get_table_names()
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0015_ref_83"


def test_copy_failure_rolls_back_new_schema_and_retry(context, monkeypatch):  # noqa: F811
    from refrncr.concept_persistence import SqlAlchemyConceptOperations

    c = context
    setup(c)
    predecessor(c)
    original = SqlAlchemyConceptOperations.create

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected concept insert failure")

    with c.engine.connect() as connection:
        with monkeypatch.context() as patch:
            patch.setattr(SqlAlchemyConceptOperations, "create", fail)
            with pytest.raises(RuntimeError, match="injected"):
                command.upgrade(config_for(connection), "head")
        assert "dataset_concepts" not in inspect(connection).get_table_names()
        assert "uq_dataset_versions_dataset_id" not in {
            i["name"] for i in inspect(connection).get_indexes("dataset_versions")
        }
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0015_ref_83"
        connection.rollback()
        command.upgrade(config_for(connection), "head")
        assert connection.scalar(text("SELECT count(*) FROM dataset_concepts")) == 1


def test_ambiguous_identifier_configuration_preflight(context):  # noqa: F811
    c = context
    setup(c, populated=False)
    predecessor(c)
    with c.engine.begin() as connection:
        connection.execute(text("UPDATE dataset_fields SET is_required=false WHERE key='code'"))
    with c.engine.connect() as connection:
        assert concept_preflight(connection) == (c.d.id,)
        connection.rollback()
        with pytest.raises(ConceptUpgradeError):
            command.upgrade(config_for(connection), "head")
        assert "dataset_concepts" not in inspect(connection).get_table_names()


@pytest.mark.parametrize("valid", [False, True])
def test_concept_cli_preflight_and_upgrade(context, valid, capsys):  # noqa: F811
    import json

    from refrncr.cli import main

    c = context
    setup(c)
    predecessor(c)
    if not valid:
        with c.engine.begin() as connection:
            connection.execute(update(DatasetRecordValue).values(canonical_value_key=b"bad"))
    args = [
        "dataset-concept-preflight",
        "--database-url",
        c.engine.url.render_as_string(hide_password=False),
    ]
    assert main(args) == (0 if valid else 1)
    assert json.loads(capsys.readouterr().out) == {"invalid_dataset_ids": [] if valid else [c.d.id]}
    with c.engine.connect() as connection:
        assert "dataset_concepts" not in inspect(connection).get_table_names()
    assert main([*args, "--upgrade"]) == (0 if valid else 1)
    capsys.readouterr()
    with c.engine.connect() as connection:
        assert ("dataset_concepts" in inspect(connection).get_table_names()) == valid
        assert connection.scalar(text("SELECT count(*) FROM dataset_versions")) == 0
        if valid:
            assert connection.scalar(text("SELECT count(*) FROM dataset_concepts")) == 1

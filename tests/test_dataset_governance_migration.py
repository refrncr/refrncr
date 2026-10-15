"""REF-73 upgrade preflight and transactional schema contracts on both engines."""

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from test_dataset_governance import governance  # noqa: F401

from refrncr import cli
from refrncr.dataset_upgrade import DatasetUpgradeError, ownership_preflight, read_owner_mapping


def migrate(engine, revision, mapping=None, downgrade=False):
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/refrncr/migrations")
    )
    config.attributes["dataset_owner_mapping"] = mapping or {}
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


def legacy(engine):
    # Fresh isolated test schema. Seed historical rows with SQL, never current ORM
    # models/services against an older schema.
    from refrncr.db.base import Base

    Base.metadata.drop_all(engine)
    migrate(engine, "0011_ref_130")
    with engine.begin() as c:
        for identifier in ("creator", "other"):
            c.execute(
                text(
                    "INSERT INTO "
                    "users(id,email,display_name,password_hash,role,is_active,is_deleted,cr"
                    "eated_at,updated_at) VALUES (:id,:email,:id,'unused','user',true,false"
                    ",CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                ),
                {"id": identifier, "email": identifier + "@example.com"},
            )
        for identifier in ("one", "two"):
            c.execute(
                text(
                    "INSERT INTO teams(id,name,name_key,created_at,updated_at) VALUES "
                    "(:id,:id,:id,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                ),
                {"id": identifier},
            )
        c.execute(
            text(
                "INSERT INTO "
                "datasets(id,key,name,description,owner_user_id,created_at,updated_at) "
                "VALUES "
                "('dataset','countries','Countries','','creator',CURRENT_TIMESTAMP,CURR"
                "ENT_TIMESTAMP)"
            )
        )
        c.execute(
            text(
                "INSERT INTO "
                "grants(id,subject_type,subject_id,resource_type,resource_id,permission"
                ",created_at,updated_at) VALUES ('old','user','creator','dataset','data"
                "set','admin',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP), ('unrelated','user'"
                ",'creator','document','doc','editor',CURRENT_TIMESTAMP,CURRENT_TIMESTA"
                "MP)"
            )
        )


def test_cli_preflight_and_explicit_upgrade(governance, tmp_path, capsys):  # noqa: F811
    engine = governance[0]
    legacy(engine)
    args = [
        "dataset-ownership-preflight",
        "--database-url",
        engine.url.render_as_string(hide_password=False),
    ]
    assert cli.main(args) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["unresolved"][0]["key"] == "countries"
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0011_ref_130"
    # The CLI upgrades through REF-83 too: supply a valid zero-Record schema.
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO dataset_fields (id,dataset_id,key,name,data_type,is_required,"
                "is_unique,display_position,created_at,updated_at) VALUES "
                "('code','dataset','code','Code','string',true,true,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dataset_business_key_fields (dataset_id,field_key) "
                "VALUES ('dataset','code')"
            )
        )
    mapping = tmp_path / "owners.json"
    mapping.write_text(json.dumps({"countries": "two"}))
    assert cli.main([*args, "--mapping", str(mapping), "--upgrade"]) == 0
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT owner_team_id FROM datasets")) == "two"


def test_cli_empty_database_preflight(tmp_path, capsys):
    assert (
        cli.main(["dataset-ownership-preflight", "--database-path", str(tmp_path / "empty.db")])
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"resolved": {}, "unresolved": [], "teams": []}


@pytest.mark.parametrize("memberships", [0, 1, 2])
def test_preflight_upgrade_and_downgrade_policy(governance, memberships):  # noqa: F811
    engine = governance[0]
    legacy(engine)
    with engine.begin() as c:
        for i, team in enumerate(("one", "two")[:memberships]):
            c.execute(
                text(
                    "INSERT INTO memberships(id,user_id,team_id,created_at) VALUES "
                    "(:id,'creator',:team,CURRENT_TIMESTAMP)"
                ),
                {"id": str(i), "team": team},
            )
    with engine.connect() as c:
        resolved, unresolved = ownership_preflight(c)
    if memberships == 1:
        assert resolved == {"dataset": "one"} and not unresolved
        migrate(engine, "0012_ref_73")
    else:
        assert unresolved[0]["key"] == "countries"
        with pytest.raises(DatasetUpgradeError):
            migrate(engine, "0012_ref_73")
        with engine.connect() as c:
            assert c.scalar(text("SELECT version_num FROM alembic_version")) == "0011_ref_130"
            assert "owner_team_id" not in {
                col["name"] for col in inspect(c).get_columns("datasets")
            }
            assert not inspect(c).has_table("dataset_export_access")
        migrate(engine, "0012_ref_73", {"countries": "two"})
    with engine.connect() as c:
        assert c.execute(text("SELECT owner_user_id, owner_team_id FROM datasets")).one() == (
            "creator",
            "one" if memberships == 1 else "two",
        )
        assert c.scalar(text("SELECT count(*) FROM dataset_export_access")) == 0
        assert c.scalar(text("SELECT count(*) FROM grants")) == 2
        columns = {col["name"]: col for col in inspect(c).get_columns("datasets")}
        assert not columns["owner_team_id"]["nullable"]
        assert not {"revision", "organisation_id", "tenant_id"} & columns.keys()
    with pytest.raises(RuntimeError, match="Cannot downgrade REF-73"):
        migrate(engine, "0011_ref_130", downgrade=True)
    with engine.begin() as c:
        c.execute(text("DELETE FROM datasets"))
        c.execute(text("DELETE FROM grants WHERE resource_type='dataset'"))
    migrate(engine, "0011_ref_130", downgrade=True)
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM grants")) == 1
        assert not inspect(c).has_table("dataset_export_access")
    migrate(engine, "0012_ref_73")


def test_invalid_mapping_and_late_ddl_failure_are_atomic(governance, monkeypatch):  # noqa: F811
    engine = governance[0]
    legacy(engine)
    for mapping in ({"unknown": "one"}, {"countries": "deleted"}):
        with pytest.raises(DatasetUpgradeError):
            migrate(engine, "0012_ref_73", mapping)
    from alembic.operations import Operations

    original = Operations.create_table

    def fail(self, name, *args, **kwargs):
        if name == "dataset_export_access":
            raise RuntimeError("simulated DDL failure")
        return original(self, name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Operations, "create_table", fail)
        with pytest.raises(RuntimeError, match="simulated DDL failure"):
            migrate(engine, "0012_ref_73", {"countries": "one"})
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "0011_ref_130"
        assert "owner_team_id" not in {col["name"] for col in inspect(c).get_columns("datasets")}
        assert c.scalar(text("SELECT count(*) FROM datasets")) == 1
    migrate(engine, "0012_ref_73", {"countries": "one"})


def test_mapping_file_rejects_duplicates_and_non_objects(tmp_path):
    path = tmp_path / "mapping.json"
    for content in ('{"countries":"one","countries":"two"}', "[]", '{"countries":null}'):
        path.write_text(content)
        with pytest.raises(DatasetUpgradeError):
            read_owner_mapping(path)
    path.write_text(json.dumps({"countries": "one"}))
    assert read_owner_mapping(path) == {"countries": "one"}


@pytest.mark.parametrize("creator_state", ["active", "inactive", "deleted"])
def test_multiple_datasets_repeated_preflight_preserves_legacy_roles(governance, creator_state):  # noqa: F811
    engine = governance[0]
    legacy(engine)
    with engine.begin() as c:
        if creator_state != "active":
            c.execute(
                text("UPDATE users SET is_active=false, is_deleted=:deleted WHERE id='creator'"),
                {"deleted": creator_state == "deleted"},
            )
        c.execute(
            text(
                "INSERT INTO memberships(id,user_id,team_id,created_at) "
                "VALUES ('m','creator','one',CURRENT_TIMESTAMP)"
            )
        )
        c.execute(
            text(
                "INSERT INTO datasets(id,key,name,description,owner_user_id,created_at,updated_at) "
                "VALUES ('second','second','Second','','other',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        for permission in ("viewer", "editor"):
            c.execute(
                text(
                    "INSERT INTO grants(id,subject_type,subject_id,resource_type,resource_id,"
                    "permission,created_at,updated_at) VALUES (:id,'team',:team,'dataset',"
                    "'dataset',:permission,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                ),
                {
                    "id": permission,
                    "permission": permission,
                    "team": "one" if permission == "viewer" else "two",
                },
            )
        original = c.execute(text("SELECT * FROM grants ORDER BY id")).all()
        datasets = c.execute(
            text("SELECT id,key,owner_user_id,created_at,updated_at FROM datasets ORDER BY id")
        ).all()
    for _ in range(2):
        with engine.connect() as c:
            resolved, unresolved = ownership_preflight(c)
            assert resolved == {"dataset": "one"}
            assert [row["key"] for row in unresolved] == ["second"]
        with pytest.raises(DatasetUpgradeError):
            migrate(engine, "0012_ref_73", {"countries": "one"})
    migrate(engine, "0012_ref_73", {"second": "two"})
    with engine.connect() as c:
        assert c.execute(text("SELECT * FROM grants ORDER BY id")).all() == original
        assert (
            c.execute(
                text("SELECT id,key,owner_user_id,created_at,updated_at FROM datasets ORDER BY id")
            ).all()
            == datasets
        )
        assert c.scalar(text("SELECT count(*) FROM teams")) == 2
        assert c.scalar(text("SELECT count(*) FROM memberships")) == 1
        assert c.scalar(text("SELECT count(*) FROM dataset_export_access")) == 0
        assert ownership_preflight(c) == ({"dataset": "one", "second": "two"}, [])

"""Index-only upgrade/downgrade preserves historical rows on SQLite and PostgreSQL."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from support import migrate

from refrncr.db.base import Base
from refrncr.models import AuditEvent


def test_chronology_index_preserves_events(database_audit_db):
    engine, _sessions, _services = database_audit_db
    Base.metadata.drop_all(engine)
    migrate(engine, "0012_ref_73")
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO users(id,email,display_name,password_hash,role,is_active,is_deleted,"
                "created_at,updated_at) VALUES "
                "('actor','test@example.com','Actor','unused','admin',"
                "true,false,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        c.execute(
            text(
                "INSERT INTO audit_events(id,timestamp,actor_user_id,object_type,object_id,action,"
                "details) VALUES ('historical',CURRENT_TIMESTAMP,'actor','old_type','legacy',"
                "'old_action',:details)"
            ),
            {"details": json.dumps({"legacy": "unchanged"})},
        )
        before = list(c.execute(select(AuditEvent.__table__)))
    for revision, downgrade, present in (
        ("head", False, True),
        ("0012_ref_73", True, False),
        ("head", False, True),
    ):
        migrate(engine, revision, downgrade=downgrade)
        indexes = {
            item["name"]: item["column_names"]
            for item in inspect(engine).get_indexes("audit_events")
        }
        assert ("ix_audit_events_timestamp_id" in indexes) == present
        if present:
            assert indexes["ix_audit_events_timestamp_id"] == ["timestamp", "id"]
        with engine.connect() as c:
            assert list(c.execute(select(AuditEvent.__table__))) == before


def test_password_recovery_migration_allows_only_the_actor_free_recovery_event(
    database_audit_db,
):
    engine, _sessions, _services = database_audit_db
    Base.metadata.drop_all(engine)
    migrate(engine, "0035_ref_294")
    user_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users(id,email,display_name,password_hash,role,is_active,is_deleted,"
                "created_at,updated_at) VALUES "
                "(:id,'admin@example.com','Admin','unused','admin',"
                "true,false,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {"id": user_id},
        )
    statement = text(
        "INSERT INTO audit_events(id,timestamp,actor_user_id,object_type,object_id,action,details) "
        "VALUES (:id,CURRENT_TIMESTAMP,NULL,'user',:user_id,'password_recovered',NULL)"
    )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(statement, {"id": str(uuid4()), "user_id": user_id})

    migrate(engine, "head")
    with engine.begin() as connection:
        connection.execute(statement, {"id": str(uuid4()), "user_id": user_id})
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_events(id,timestamp,actor_user_id,object_type,object_id,"
                "action,details) VALUES "
                "(:id,CURRENT_TIMESTAMP,NULL,'user',:user_id,'created',NULL)"
            ),
            {"id": str(uuid4()), "user_id": user_id},
        )

    with pytest.raises(RuntimeError, match="Password recovery audit history exists"):
        migrate(engine, "0035_ref_294", downgrade=True)

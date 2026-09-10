"""Index-only upgrade/downgrade preserves historical rows on SQLite and PostgreSQL."""

import json

from sqlalchemy import inspect, select, text
from test_audit_coverage import audit_db as audit_db
from test_dataset_governance_migration import migrate

from refrncr.db.base import Base
from refrncr.models import AuditEvent


def test_chronology_index_preserves_events(audit_db):
    engine, _sessions, _services = audit_db
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

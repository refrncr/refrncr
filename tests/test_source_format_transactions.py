"""REF-154 engine parity for migrations, privileges, races and atomic preferences."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from dataset_helpers import create_owned_dataset
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from support import migrate

from refrncr.db.base import Base
from refrncr.models import AuditEvent, DatasetField, DatasetFieldSourceFormat, DatasetRecord
from refrncr.services import NotFoundError, ValidationError


def setup(audit_db):
    engine, factory, s = audit_db
    a = s.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    d = create_owned_dataset(s.datasets, a.id, "sources", "Sources", "")
    s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True, False, True)
    s.dataset_fields.create_field(a.id, d.key, "amount", "Amount", "number", False, False)
    return engine, factory, s, a, d


def stage(s, a, d, session, code, value, profile):
    state, _, _ = s.record_imports.begin(
        a.id, session, d.key, "values.csv", f"code;amount\n{code};{value}\n".encode()
    )
    result = s.record_imports.preview(
        a.id,
        session,
        d.key,
        state.token,
        ("code", "amount"),
        format_components=({"source": "v1-text"}, {"source": profile}),
    )
    assert result.valid
    return state


def snapshot(engine):
    with engine.connect() as c:
        return {
            t.name: sorted((tuple(row) for row in c.execute(select(t))), key=repr)
            for t in Base.metadata.sorted_tables
        }


def test_concurrent_confirmation_has_one_atomic_preference_winner(database_audit_db):
    engine, factory, s, a, d = setup(database_audit_db)
    states = [
        stage(s, a, d, "one", "same", "1.234", "v1-eu"),
        stage(s, a, d, "two", "same", "1,234", "v1-us"),
    ]
    barrier = Barrier(2)

    def run(index):
        state = states[index]
        barrier.wait(timeout=10)
        try:
            return s.record_imports.confirm(a.id, state.session_id, d.key, state.token)
        except ValidationError:
            return 0

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert sorted(results) == [0, 1]
    with factory() as session:
        assert len(list(session.scalars(select(DatasetRecord)))) == 1
        assert (
            len(
                list(
                    session.scalars(
                        select(AuditEvent).where(AuditEvent.action == "dataset.records_imported")
                    )
                )
            )
            == 1
        )
        proposals = list(session.scalars(select(DatasetFieldSourceFormat)))
        winner = "v1-eu" if results[0] else "v1-us"
        assert {p.choice["profile_id"] for p in proposals} == {"v1-text", winner}


def test_failed_confirmation_preserves_previous_proposal_and_all_tables(
    database_audit_db, monkeypatch
):
    engine, factory, s, a, d = setup(database_audit_db)
    first = stage(s, a, d, "one", "first", "1.234,56", "v1-eu")
    s.record_imports.confirm(a.id, "one", d.key, first.token)
    second = stage(s, a, d, "two", "second", "1,234.56", "v1-us")
    before = snapshot(engine)
    import refrncr.record_import as module

    def fail(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(module, "record_audit", fail)
    with pytest.raises(RuntimeError, match="injected"):
        s.record_imports.confirm(a.id, "two", d.key, second.token)
    assert snapshot(engine) == before


def test_import_choice_grants_no_additional_authority(database_audit_db):
    engine, factory, s, a, d = setup(database_audit_db)
    member = s.users.create_user(a.id, "member@example.com", "Member", "valid password")
    denied = s.users.create_user(a.id, "reader@example.com", "Reader", "valid password")
    s.memberships.create_membership(a.id, member.id, d.owner_team_id)
    state = stage(s, member, d, "member", "A", "1.234,56", "v1-eu")
    for actor, session, key in [
        (denied, "member", d.key),
        (a, "other", d.key),
        (member, "member", "unknown"),
    ]:
        with pytest.raises(NotFoundError):
            s.record_imports.format_assistance(actor.id, session, key, state.token)
    before = snapshot(engine)
    assert (
        s.record_imports.format_assistance(member.id, "member", d.key, state.token)[1]["choice"][
            "profile_id"
        ]
        == "v1-eu"
    )
    assert snapshot(engine) == before
    with factory() as session:
        fields_before = session.execute(select(DatasetField.__table__)).all()
    capabilities_before = (
        s.authorization.datasets.can_maintain_dataset(member.id, d.id),
        s.authorization.datasets.can_change_dataset_owner(member.id, d.id),
        s.authorization.datasets.can_manage_export_access(member.id, d.id),
    )
    assert capabilities_before == (True, False, False)
    assert s.record_imports.confirm(member.id, "member", d.key, state.token) == 1
    assert next(user for user in s.users.list_users(a.id) if user.id == member.id).role == "user"
    with factory() as session:
        assert session.execute(select(DatasetField.__table__)).all() == fields_before
    assert (
        s.authorization.datasets.can_maintain_dataset(member.id, d.id),
        s.authorization.datasets.can_change_dataset_owner(member.id, d.id),
        s.authorization.datasets.can_manage_export_access(member.id, d.id),
    ) == capabilities_before
    with pytest.raises(NotFoundError):
        s.datasets.change_owner(member.id, d.key, d.owner_team_id, d.owner_team_id)
    with pytest.raises(NotFoundError):
        s.datasets.grant_export_access(member.id, d.key, "user", denied.id)


@pytest.mark.slow
def test_additive_migration_preserves_business_rows_and_downgrade(database_audit_db):
    engine, _, _ = database_audit_db
    Base.metadata.drop_all(engine)
    migrate(engine, "head")
    # Populate at the current schema, then construct the historical migration fixture.
    engine, factory, s, a, d = setup(database_audit_db)
    s.dataset_records.create_record(a.id, d.key, {"code": "kept", "amount": "123.45"})
    migrate(engine, "0016_ref_84", downgrade=True)
    with engine.connect() as c:
        columns = {
            t: ", ".join(v["name"] for v in inspect(engine).get_columns(t))
            for t in inspect(engine).get_table_names()
        }
        before = {
            t: c.execute(text("SELECT * FROM " + t)).all()
            for t in inspect(engine).get_table_names()
            if t != "alembic_version"
        }
    migrate(engine, "head")
    assert "dataset_field_source_formats" in inspect(engine).get_table_names()
    with engine.connect() as c:
        for table, rows in before.items():
            assert c.execute(text("SELECT " + columns[table] + " FROM " + table)).all() == rows
    state = stage(s, a, d, "after", "new", "1.234,56", "v1-eu")
    s.record_imports.confirm(a.id, "after", d.key, state.token)
    with factory() as session:
        assert list(session.scalars(select(DatasetFieldSourceFormat)))
    migrate(engine, "0016_ref_84", downgrade=True)
    assert "dataset_field_source_formats" not in inspect(engine).get_table_names()
    with factory() as session:
        assert session.scalar(text("SELECT count(*) FROM dataset_records")) == 2
    migrate(engine, "head")
    with factory() as session:
        assert list(session.scalars(select(DatasetFieldSourceFormat))) == []
        assert session.scalar(text("SELECT count(*) FROM dataset_records")) == 2


def test_new_concept_preserves_proposal_for_same_field_identity(database_audit_db):
    from datetime import UTC, datetime

    from refrncr.models import Dataset

    engine, factory, s, a, d = setup(database_audit_db)
    state = stage(s, a, d, "first", "A", "1.234,56", "v1-eu")
    s.record_imports.confirm(a.id, "first", d.key, state.token)
    with factory() as session:
        before = {
            p.field_id: dict(p.choice) for p in session.scalars(select(DatasetFieldSourceFormat))
        }
        revision = session.get(Dataset, d.id).revision
    s.dataset_versions.publish(a.id, d.id, datetime.now(UTC).date(), "Published", revision)
    s.dataset_versions.create_concept(a.id, d.id)
    with factory() as session:
        after = {
            p.field_id: dict(p.choice) for p in session.scalars(select(DatasetFieldSourceFormat))
        }
    assert before == after
    second, _, _ = s.record_imports.begin(
        a.id, "second", d.key, "new.csv", b"code;amount\nB;1.234\n"
    )
    assert (
        s.record_imports.format_assistance(a.id, "second", d.key, second.token)[1]["choice"][
            "profile_id"
        ]
        == "v1-eu"
    )


def test_migrated_proposal_constraints_and_field_deletion(database_audit_db):
    from uuid import uuid4

    engine, _, _ = database_audit_db
    Base.metadata.drop_all(engine)
    migrate(engine, "head")
    engine, factory, s, a, d = setup(database_audit_db)
    inspector = inspect(engine)
    table = "dataset_field_source_formats"
    assert inspector.get_pk_constraint(table)["constrained_columns"] == ["field_id"]
    foreign_key = inspector.get_foreign_keys(table)[0]
    assert foreign_key["referred_table"] == "dataset_fields"
    assert foreign_key["referred_columns"] == ["id"]
    assert foreign_key["options"]["ondelete"] == "CASCADE"
    assert all(not column["nullable"] for column in inspector.get_columns(table))
    with factory() as session:
        field = session.scalar(select(DatasetField).where(DatasetField._key == "amount"))
        field_id = field.id
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            DatasetFieldSourceFormat.__table__.insert(),
            {"field_id": str(uuid4()), "choice": {"profile_id": "v1-eu"}},
        )
    with engine.begin() as connection:
        connection.execute(
            DatasetFieldSourceFormat.__table__.insert(),
            {"field_id": field_id, "choice": {"profile_id": "v1-eu"}},
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            DatasetFieldSourceFormat.__table__.insert(),
            {"field_id": field_id, "choice": {"profile_id": "v1-us"}},
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(f"UPDATE {table} SET choice = NULL WHERE field_id = :id"), {"id": field_id}
        )
    s.dataset_fields.delete_field(a.id, d.key, "amount")
    with factory() as session:
        assert session.get(DatasetFieldSourceFormat, field_id) is None


@pytest.mark.parametrize("ending", ["cancel", "expiry", "validation"])
def test_unsuccessful_attempt_retains_previous_confirmed_proposal(database_audit_db, ending):
    from refrncr.record_import import TransientImportStore

    engine, factory, s, a, d = setup(database_audit_db)
    clock = [0.0]
    s.record_imports.store = TransientImportStore(clock=lambda: clock[0])
    first = stage(s, a, d, "first", "saved", "1.234,56", "v1-eu")
    assert s.record_imports.confirm(a.id, "first", d.key, first.token) == 1
    before = snapshot(engine)
    second = stage(s, a, d, "second", "new", "1,234.56", "v1-us")
    if ending == "cancel":
        s.record_imports.cancel(a.id, "second", d.key, second.token)
    elif ending == "expiry":
        clock[0] = 901
        with pytest.raises(NotFoundError):
            s.record_imports.confirm(a.id, "second", d.key, second.token)
    else:
        result = s.record_imports.preview(
            a.id,
            "second",
            d.key,
            second.token,
            ("code", "amount"),
            format_components=({"source": "v1-text"}, {"source": "v1-eu"}),
        )
        assert not result.valid
    assert snapshot(engine) == before

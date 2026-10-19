"""REF-83 complete snapshot, lifecycle and transaction evidence on both engines."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta
from threading import Barrier, Event, local
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from support import NOW, MutableClock, audits, content, publish, revision, setup, snapshot, versions

from refrncr.dataset_versions import DatasetVersionService
from refrncr.models import DatasetVersion, DatasetVersionField, DatasetVersionValue
from refrncr.persistence import SqlAlchemyUnitOfWork
from refrncr.ports import PersistenceConflictError
from refrncr.services import ConflictError, NotFoundError, ValidationError
from refrncr.version_contracts import version_states
from refrncr.version_persistence import SqlAlchemyVersionOperations


def test_snapshot_lifecycle_and_no_change(context):
    c = context
    setup(c)
    before = snapshot(c)
    review = c.service.review(c.a.id, c.d.id)
    assert snapshot(c) == before
    assert review[3].fields_added == 2 and review[3].records_added == 1
    first = publish(c)
    saved = content(c, first)
    assert saved.name == c.d.name and saved.description == c.d.description
    assert saved.identifier_keys == ("code",)
    assert saved.records[0].id == c.record.id
    assert first.number == 1 and first.publisher_id == c.a.id
    with pytest.raises(FrozenInstanceError):
        first.name = "tampered"
    with pytest.raises(ConflictError, match="concept"):
        publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    with pytest.raises(ConflictError, match="matches"):
        publish(c)
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Updated", "new description")
    with pytest.raises(ConflictError, match="matches"):
        publish(c)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Updated", "string", False, False)
    second = publish(c, day=NOW.date() + timedelta(days=1))
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Later"}
    )
    assert content(c, first) == saved
    assert content(c, second).records == saved.records
    assert {s.version.number: s.status for s in version_states(versions(c), NOW.date())} == {
        1: "Current",
        2: "Scheduled",
    }
    with pytest.raises(ConflictError, match="Scheduled"):
        publish(c)
    c.service.cancel(c.a.id, c.d.id, second.id)
    with pytest.raises(ConflictError, match="cancelled"):
        publish(c)
    assert c.service.concept_context(c.a.id, c.d.key).stale
    assert content(c, second).name == "Updated"
    assert {s.version.number: s.status for s in version_states(versions(c), NOW.date())} == {
        1: "Current",
        2: "Cancelled",
    }
    events = [e for e in audits(c) if e.action.startswith("dataset.version_")]
    assert [e.action for e in events].count("dataset.version_published") == 2
    assert [e.action for e in events].count("dataset.version_cancelled") == 1
    assert all(
        "Café" not in str(e.details) and c.d.description not in str(e.details) for e in events
    )


def test_publication_rejects_duplicate_effective_date(database_context):
    c = database_context
    setup(c)
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Changed"}
    )
    with pytest.raises(ValidationError, match="already begins"):
        publish(c, day=NOW.date())
    assert len(versions(c)) == 1


@pytest.mark.parametrize(
    "kind", ["owner", "admin", "viewer", "editor", "export", "outsider", "inactive", "deleted"]
)
def test_authorization(context, kind):
    c = context
    setup(c)
    actor = c.u
    if kind == "admin":
        actor = c.a
    if kind in {"owner", "inactive", "deleted"}:
        c.s.memberships.create_membership(c.a.id, actor.id, c.owner.id)
    if kind in {"viewer", "editor"}:
        c.s.grants.create_grant(c.a.id, "user", actor.id, "dataset", c.d.id, kind)
    if kind == "export":
        c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", actor.id)
    if kind == "inactive":
        c.s.users.disable_user(c.a.id, actor.id)
    if kind == "deleted":
        c.s.users.delete_user(c.a.id, actor.id)
    day = NOW.date() + timedelta(days=1)
    if kind in {"owner", "admin"}:
        v = publish(c, actor=actor.id, day=day)
        c.service.cancel(actor.id, c.d.id, v.id)
        assert versions(c)[0].cancelled_at is not None
    else:
        v = publish(c, day=day)
        before = snapshot(c)
        for dataset_id in (c.d.id, str(uuid4())):
            with pytest.raises(NotFoundError):
                c.service.publish(actor.id, dataset_id, day, "", 1)
            with pytest.raises(NotFoundError):
                c.service.cancel(actor.id, dataset_id, v.id)
        assert snapshot(c) == before


@pytest.mark.parametrize("kind", ["audit", "snapshot", "commit", "constraint"])
def test_rollback(database_context, monkeypatch, kind):
    c = database_context
    setup(c)
    before = snapshot(c)
    original = SqlAlchemyVersionOperations.append

    def fail(*args, **kwargs):
        raise OperationalError("private SQL", {}, Exception("secret"))

    if kind == "snapshot":

        def partial(*args, **kwargs):
            original(*args, **kwargs)
            fail()

        monkeypatch.setattr(SqlAlchemyVersionOperations, "append", partial)
    elif kind == "audit":
        monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
    elif kind == "commit":
        monkeypatch.setattr(Session, "commit", fail)
    else:
        original_audit = SqlAlchemyUnitOfWork.add_audit

        def invalid_actor(self, **values):
            original_audit(self, **(values | {"actor_user_id": str(uuid4())}))

        monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", invalid_actor)
    with pytest.raises(ConflictError) as error:
        publish(c)
    assert "secret" not in str(error.value) and "SQL" not in str(error.value)
    assert snapshot(c) == before
    monkeypatch.undo()
    assert publish(c).number == 1


def test_concurrent_same_revision(database_context):
    c = database_context
    setup(c)
    expected = revision(c)
    barrier = Barrier(2)

    def run():
        barrier.wait(timeout=10)
        try:
            return publish(c, expected=expected)
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sum(v is not None for v in results) == 1
    assert len(versions(c)) == 1
    assert len([e for e in audits(c) if e.action == "dataset.version_published"]) == 1
    with pytest.raises(ConflictError):
        publish(c, expected=expected)


def test_canonical_revert_and_administration_are_not_changes(context):
    c = context
    setup(c)
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "different"}
    )
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 2, {"code": "a", "label": " Cafe\u0301 "}
    )
    c.s.datasets.change_owner(c.a.id, c.d.key, c.owner.id, c.other.id)
    c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", c.u.id)
    with pytest.raises(ConflictError, match="matches"):
        publish(c)


def test_date_boundaries_and_deletion(context):
    c = context
    setup(c, populated=False)
    with pytest.raises(ValidationError):
        publish(c, day=NOW.date() - timedelta(days=1))
    future = NOW.date() + timedelta(days=1)
    v = publish(c, day=future)
    assert not any(s.status == "Current" for s in version_states(versions(c), NOW.date()))
    c.service.clock.set(NOW + timedelta(days=1))
    assert version_states(versions(c), future)[0].status == "Current"
    with pytest.raises(ConflictError):
        c.service.cancel(c.a.id, c.d.id, v.id)
    before_audit = len(audits(c))
    target = c.s.datasets.get_dataset_for_deletion(c.a.id, c.d.id)
    c.s.datasets.delete_dataset(c.a.id, c.d.id, target.name, target.expected_updated_at)
    with c.sessions() as session:
        for model in (DatasetVersion, DatasetVersionField, DatasetVersionValue):
            assert not session.scalars(select(model)).all()
    assert len(audits(c)) == before_audit + 1


def test_persisted_snapshot_cannot_be_edited(database_context):
    c = database_context
    setup(c)
    v = publish(c)
    before = snapshot(c)
    with pytest.raises(PersistenceConflictError), c.sessions.begin() as session:
        session.get(DatasetVersion, v.id).name = "changed"
    with pytest.raises(PersistenceConflictError), c.sessions.begin() as session:
        session.delete(session.get(DatasetVersion, v.id))
    assert snapshot(c) == before


@pytest.mark.parametrize(
    "data_type,raw,canonical",
    [
        ("string", " Cafe\u0301 ", "Café"),
        ("integer", "00012", "12"),
        ("number", "12.3400", "12.34"),
        ("float", "1.2500e2", "125.0"),
        ("boolean", "false", "false"),
        ("date", "2028-02-29", "2028-02-29"),
        ("time", "12:30:00.500000", "12:30:00.5"),
        ("datetime", "2026-09-10T12:30:00.500000", "2026-09-10T12:30:00.5"),
        ("timestamp", "2026-09-10T12:30:00+02:00", "2026-09-10T10:30:00Z"),
        ("string", "", None),
        ("string", None, None),
    ],
)
def test_datatypes_and_null_snapshot(context, data_type, raw, canonical):
    from refrncr.datatypes import get_datatype

    c = context
    setup(c, populated=False)
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "value", "Value", data_type)
    record = c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "a", "value": raw})
    before = snapshot(c)
    v = publish(c)
    values = {value.field_key: value for value in content(c, v).records[0].values}
    assert (
        get_datatype(data_type).text(values["value"].value) if "value" in values else None
    ) == canonical
    after = snapshot(c)
    assert before["dataset_record_values"] == after["dataset_record_values"]
    assert before["dataset_records"] == after["dataset_records"]
    assert content(c, v).records[0].id == record.id


@pytest.mark.parametrize(
    "invalid", ["fields", "identifier", "required", "value", "note", "revision"]
)
def test_invalid_publication_is_atomic(database_context, invalid):
    from sqlalchemy import delete, update

    from refrncr.models import DatasetBusinessKeyField, DatasetRecordValue

    c = database_context
    c.service = DatasetVersionService(
        c.s.datasets.uow, c.s.authorization.datasets, clock=MutableClock(NOW)
    )
    if invalid != "fields":
        setup(c)
    if invalid == "identifier":
        with c.sessions.begin() as session:
            session.execute(delete(DatasetBusinessKeyField))
    elif invalid == "required":
        with c.sessions.begin() as session:
            session.execute(delete(DatasetRecordValue))
    elif invalid == "value":
        with c.sessions.begin() as session:
            session.execute(update(DatasetRecordValue).values(canonical_value_key=b"invalid"))
    before = snapshot(c)
    with pytest.raises((ValidationError, ConflictError)):
        c.service.publish(
            c.a.id,
            c.d.id,
            NOW.date(),
            "n" * 2001 if invalid == "note" else "",
            -1 if invalid == "revision" else revision(c),
        )
    assert snapshot(c) == before


@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_cancellation_rollback_and_retry(database_context, monkeypatch, failure):
    c = database_context
    setup(c)
    v = publish(c, day=NOW.date() + timedelta(days=1))
    before = snapshot(c)

    def fail(*args, **kwargs):
        raise OperationalError("private", {}, Exception("private"))

    with monkeypatch.context() as patch:
        patch.setattr(
            SqlAlchemyUnitOfWork if failure == "audit" else Session,
            "add_audit" if failure == "audit" else "commit",
            fail,
        )
        with pytest.raises(ConflictError):
            c.service.cancel(c.a.id, c.d.id, v.id)
    assert snapshot(c) == before
    c.service.cancel(c.a.id, c.d.id, v.id)
    after = snapshot(c)
    with pytest.raises(ConflictError):
        c.service.cancel(c.a.id, c.d.id, v.id)
    assert snapshot(c) == after
    assert len([e for e in audits(c) if e.action == "dataset.version_cancelled"]) == 1


@contextmanager
def observe_writer_lock(engine):
    """Coordinate at the real competing SQL lock attempt, never with a sleep."""
    attempted, state = Event(), local()

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        sql = " ".join(statement.upper().split())
        if getattr(state, "competing", False) and (
            sql == "BEGIN IMMEDIATE" or ("FROM DATASETS" in sql and "FOR UPDATE" in sql)
        ):
            attempted.set()

    event.listen(engine, "before_cursor_execute", observe)
    try:
        yield attempted, state
    finally:
        event.remove(engine, "before_cursor_execute", observe)


@pytest.mark.parametrize("mutation", ["metadata", "field", "record", "import", "delete"])
@pytest.mark.parametrize("first", ["publish", "mutate"])
def test_publication_serializes_with_working_mutations(context, monkeypatch, mutation, first):
    c = context
    setup(c)
    expected = revision(c)
    target = c.s.datasets.get_dataset_for_deletion(c.a.id, c.d.id)
    state = None
    if mutation == "import":
        state, _, _ = c.s.record_imports.begin(
            c.a.id, "session", c.d.key, "rows.csv", b"code,label\nb,Imported\n"
        )
        c.s.record_imports.preview(c.a.id, "session", c.d.key, state.token, ("code", "label"))
    held, release = Event(), Event()
    original = SqlAlchemyUnitOfWork.add_audit
    expected_action = (
        "dataset.version_published"
        if first == "publish"
        else {
            "metadata": "metadata_updated",
            "field": "definition_updated",
            "record": "updated",
            "import": "dataset.records_imported",
            "delete": "dataset.deleted",
        }[mutation]
    )

    def hold(self, **values):
        original(self, **values)
        if values["action"] == expected_action:
            held.set()
            assert release.wait(timeout=15)

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", hold)

    def mutate():
        if mutation == "metadata":
            return c.s.datasets.update_dataset(c.a.id, c.d.key, "Changed", "")
        if mutation == "field":
            return c.s.dataset_fields.update_field(
                c.a.id, c.d.key, "label", "Changed", "string", False, False
            )
        if mutation == "record":
            return c.s.dataset_records.update_record(
                c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Changed"}
            )
        if mutation == "import":
            return c.s.record_imports.confirm(c.a.id, "session", c.d.key, state.token)
        return c.s.datasets.delete_dataset(c.a.id, c.d.id, target.name, target.expected_updated_at)

    operations = {"publish": lambda: publish(c, expected=expected), "mutate": mutate}
    second = "mutate" if first == "publish" else "publish"

    def run_second():
        thread_state.competing = True
        return operations[second]()

    with (
        observe_writer_lock(c.engine) as (attempted, thread_state),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        winning = pool.submit(operations[first])
        assert held.wait(timeout=10)
        losing = pool.submit(run_second)
        try:
            assert attempted.wait(timeout=10)
        finally:
            release.set()
        winning.result(timeout=20)
        if mutation == "metadata":
            losing.result(timeout=20)
        else:
            with pytest.raises((ConflictError, NotFoundError, ValidationError)):
                losing.result(timeout=20)
    if mutation == "metadata":
        assert len(versions(c)) == 1
        assert content(c, versions(c)[0]).name == (c.d.name if first == "publish" else "Changed")
        assert c.s.datasets.get_dataset_configuration(c.a.id, c.d.key).dataset.name == "Changed"
    elif first == "publish":
        assert len(versions(c)) == 1
        assert content(c, versions(c)[0]).name == c.d.name
        assert len(content(c, versions(c)[0]).records) == 1
    else:
        assert len(versions(c)) == 0
    assert len([e for e in audits(c) if e.action == "dataset.version_published"]) == (
        first == "publish" or mutation == "metadata"
    )


@pytest.mark.parametrize("first", ["cancel", "publish"])
def test_cancellation_and_publication_serialize(context, monkeypatch, first):
    c = context
    setup(c)
    scheduled = publish(c, day=NOW.date() + timedelta(days=1))
    c.service.create_concept(c.a.id, c.d.id)
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Changed", "")
    expected = revision(c)
    held, release = Event(), Event()
    original = SqlAlchemyUnitOfWork.add_audit
    if first == "publish":
        # Existing scheduled version blocks the new publication without touching cancellation.
        with pytest.raises(ConflictError):
            publish(c, expected=expected)
        c.service.cancel(c.a.id, c.d.id, scheduled.id)
    else:

        def hold(self, **values):
            original(self, **values)
            if values["action"] == "dataset.version_cancelled":
                held.set()
                assert release.wait(timeout=15)

        monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", hold)

        def competing_publish():
            thread_state.competing = True
            return publish(c, expected=expected)

        with (
            observe_writer_lock(c.engine) as (attempted, thread_state),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            cancelling = pool.submit(c.service.cancel, c.a.id, c.d.id, scheduled.id)
            assert held.wait(timeout=10)
            publishing = pool.submit(competing_publish)
            try:
                assert attempted.wait(timeout=10)
            finally:
                release.set()
            cancelling.result(timeout=20)
            with pytest.raises(ConflictError):
                publishing.result(timeout=20)
    with pytest.raises(ConflictError, match="cancelled"):
        publish(c)
    assert c.service.concept_context(c.a.id, c.d.key).stale
    assert len([e for e in audits(c) if e.action == "dataset.version_cancelled"]) == 1


def test_version_database_constraints(database_context):
    from sqlalchemy import insert
    from sqlalchemy.exc import IntegrityError

    from refrncr.models import DatasetVersionRecord

    c = database_context
    setup(c)
    v = publish(c)
    before = snapshot(c)
    with c.sessions() as session:
        metadata = dict(session.execute(select(DatasetVersion.__table__)).one()._mapping)
    with pytest.raises(IntegrityError), c.sessions.begin() as session:
        session.execute(insert(DatasetVersion).values(**(metadata | {"id": str(uuid4())})))
    # Source Field IDs cannot be attached to a foreign or missing version.
    with pytest.raises(IntegrityError), c.sessions.begin() as session:
        session.add(
            DatasetVersionValue(
                version_id=str(uuid4()),
                record_id=c.record.id,
                field_id=content(c, v).fields[0].id,
                canonical_key=b"sa",
                canonical_text="a",
            )
        )
    with pytest.raises(IntegrityError), c.sessions.begin() as session:
        session.add(DatasetVersionRecord(version_id=v.id, id=str(uuid4()), identifier_key=b""))
    assert snapshot(c) == before


def test_clock_uses_utc_calendar(database_context):
    c = database_context
    setup(c)
    c.service.clock = MutableClock(datetime(2026, 9, 10, 22, 30, tzinfo=UTC))
    v = publish(c)
    assert v.published_at.date() == NOW.date()
    assert c.service.clock.today() == NOW.date()


def test_effective_date_uses_utc_day_at_amsterdam_midnight(database_context):
    c = database_context
    setup(c)
    amsterdam = datetime(2027, 1, 1, 0, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))
    clock = MutableClock(amsterdam.astimezone(UTC))
    c.service.clock = clock
    version = publish(c, day=date(2027, 1, 1))

    assert clock.now() == datetime(2026, 12, 31, 23, 30, tzinfo=UTC)
    assert clock.today() == date(2026, 12, 31)
    with pytest.raises(NotFoundError):
        c.service.read_current(c.a.id, c.d.key)

    clock.set(datetime(2027, 1, 1, tzinfo=UTC))
    current = c.service.read_current(c.a.id, c.d.key)
    assert current.publications[0].version.id == version.id


def test_persisted_version_timestamps_are_utc(database_context):
    c = database_context
    setup(c)
    version = publish(c, day=NOW.date() + timedelta(days=1))
    loaded = versions(c)[0]
    assert loaded.published_at == version.published_at == NOW
    assert loaded.published_at.tzinfo is UTC
    c.service.cancel(c.a.id, c.d.id, version.id)
    loaded = versions(c)[0]
    assert loaded.cancelled_at == NOW and loaded.cancelled_at.tzinfo is UTC


def test_publication_review_has_bounded_read_queries(context):
    c = context
    setup(c)
    statements = []

    def collect(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    def review_count():
        statements.clear()
        event.listen(c.engine, "before_cursor_execute", collect)
        try:
            result = c.service.review(c.a.id, c.d.id)
        finally:
            event.remove(c.engine, "before_cursor_execute", collect)
        return len(statements), result

    small_count, small = review_count()
    assert len(small[2].records) == 1
    source = "code,label\n" + "".join(f"item{i},Value {i}\n" for i in range(100))
    state, _, _ = c.s.record_imports.begin(c.a.id, "session", c.d.key, "rows.csv", source.encode())
    c.s.record_imports.preview(c.a.id, "session", c.d.key, state.token, ("code", "label"))
    c.s.record_imports.confirm(c.a.id, "session", c.d.key, state.token)
    before = snapshot(c)
    large_count, large = review_count()
    assert len(large[2].records) == large[3].records_added == 101
    # Includes the existing authorization reads/locks, not just snapshot content.
    assert large_count == small_count and large_count <= 12
    assert snapshot(c) == before

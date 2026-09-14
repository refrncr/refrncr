"""REF-141 real persistence, authorization, rollback and concurrent lifecycle contracts."""

import json
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from test_audit_coverage import audit_db, audits, context, snapshot  # noqa: F401
from werkzeug.datastructures import MultiDict

from refrncr.audit import AuditContractError, validate_event
from refrncr.audit_history import AuditHistoryService
from refrncr.db.base import Base
from refrncr.models import (
    AuditEvent,
    Dataset,
    DatasetBusinessKeyField,
    DatasetExportAccess,
    DatasetField,
    DatasetRecord,
    DatasetRecordValue,
    Grant,
)
from refrncr.persistence import SqlAlchemyDatasetOperations, SqlAlchemyUnitOfWork
from refrncr.ports import PersistenceConflictError
from refrncr.services import ConflictError, NotFoundError, ValidationError
from refrncr.web import SESSION_COOKIE, create_flask_app


def deletion(c, actor=None):
    return c.s.datasets.get_dataset_for_deletion(actor or c.a.id, c.d.id)


def remove(c, target=None, actor=None, name=None):
    target = target or deletion(c)
    return c.s.datasets.delete_dataset(
        actor or c.a.id,
        target.id,
        target.name if name is None else name,
        target.expected_updated_at,
    )


def populate(c, records=2):
    s, a, d = c.s, c.a, c.d
    s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True, True, True)
    s.dataset_fields.create_field(a.id, d.key, "label", "Label", "string")
    for i in range(records):
        s.dataset_records.create_record(a.id, d.key, {"code": str(i), "label": "private-value"})
    s.datasets.grant_export_access(a.id, d.key, "user", c.u.id)
    s.datasets.grant_export_access(a.id, d.key, "team", c.other.id)
    with c.sessions.begin() as session:
        session.add_all(
            [
                Grant(
                    subject_type="user",
                    subject_id=c.u.id,
                    resource_type="dataset",
                    resource_id=d.id,
                    permission="editor",
                ),
                Grant(
                    subject_type="team",
                    subject_id=c.other.id,
                    resource_type="dataset",
                    resource_id=d.id,
                    permission="admin",
                ),
                Grant(
                    subject_type="user",
                    subject_id=c.u.id,
                    resource_type="document",
                    resource_id=d.id,
                    permission="viewer",
                ),
            ]
        )


@pytest.mark.parametrize("records", [None, 0, 2])
def test_atomic_deletion_preserves_history(context, records):  # noqa: F811
    c = context
    if records is not None:
        populate(c, records)
    other = c.s.datasets.create_dataset(c.a.id, "other", "Other", "", c.owner.id)
    before = snapshot(c)
    target = deletion(c)
    assert (target.field_count, target.record_count) == (0 if records is None else 2, records or 0)
    assert snapshot(c) == before  # Reading confirmation never writes/audits.
    result = remove(c, target)
    assert result == target
    after = snapshot(c)
    assert set(before["audit_events"]) < set(after["audit_events"])
    assert len(after["audit_events"]) == len(before["audit_events"]) + 1
    for name in ("users", "teams", "memberships", "login_sessions"):
        assert after[name] == before[name]
    with c.sessions() as session:
        assert session.get(Dataset, c.d.id) is None
        assert session.get(Dataset, other.id) is not None
        for model in (
            DatasetField,
            DatasetBusinessKeyField,
            DatasetRecord,
            DatasetRecordValue,
            DatasetExportAccess,
        ):
            assert not session.scalars(select(model).where(model.dataset_id == c.d.id)).all()
        grants = session.scalars(select(Grant)).all()
        assert all(g.resource_type == "document" for g in grants)
        assert len(grants) == (0 if records is None else 1)
        if c.engine.dialect.name == "sqlite":
            assert session.scalar(text("PRAGMA foreign_keys")) == 1
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
    e = audits(c)[-1]
    assert (e.actor_user_id, e.object_type, e.object_id, e.action, e.details) == (
        c.a.id,
        "dataset",
        c.d.id,
        "dataset.deleted",
        {"dataset_name": c.d.name},
    )
    assert "private-value" not in json.dumps(e.details)
    assert c.d.description not in json.dumps(e.details)


@pytest.mark.parametrize(
    "name", ["café", "CAFÉ", " Café", "Café ", "Cafe\u0301", "Café\u200b", "", None, 1]
)
def test_confirmation_is_exact_codepoints(context, name):  # noqa: F811
    c = context
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Café", "")
    target = deletion(c)
    before = snapshot(c)
    with pytest.raises(ValidationError):
        c.s.datasets.delete_dataset(c.a.id, c.d.id, name, target.expected_updated_at)
    assert snapshot(c) == before
    remove(c, target, name="Café")


@pytest.mark.parametrize(
    "kind",
    [
        "outsider",
        "viewer",
        "editor",
        "legacy_admin",
        "export_user",
        "export_team",
        "inactive",
        "deleted",
        "missing",
        "owner",
        "admin",
    ],
)
def test_authorization_and_non_disclosing_targets(context, kind):  # noqa: F811
    c = context
    actor = c.u.id
    if kind in {"viewer", "editor", "legacy_admin"}:
        with c.sessions.begin() as session:
            session.add(
                Grant(
                    subject_type="user",
                    subject_id=actor,
                    resource_type="dataset",
                    resource_id=c.d.id,
                    permission=kind.replace("legacy_", ""),
                )
            )
    if kind in {"export_user", "export_team"}:
        c.s.datasets.grant_export_access(
            c.a.id,
            c.d.key,
            "user" if kind == "export_user" else "team",
            actor if kind == "export_user" else c.other.id,
        )
        c.s.memberships.create_membership(c.a.id, actor, c.other.id)
    if kind in {"owner", "inactive", "deleted"}:
        c.s.memberships.create_membership(c.a.id, actor, c.owner.id)
    if kind == "inactive":
        c.s.users.disable_user(c.a.id, actor)
    if kind == "deleted":
        c.s.users.delete_user(c.a.id, actor)
    if kind == "missing":
        actor = str(uuid4())
    if kind == "admin":
        actor = c.a.id
    target = deletion(c)
    if kind in {"owner", "admin"}:
        assert deletion(c, actor).id == c.d.id
        remove(c, target, actor)
        assert audits(c)[-1].actor_user_id == actor
        return
    before = snapshot(c)
    for dataset_id in (c.d.id, str(uuid4()), "malformed"):
        for call in (
            lambda dataset_id=dataset_id: c.s.datasets.get_dataset_for_deletion(actor, dataset_id),
            lambda dataset_id=dataset_id: c.s.datasets.delete_dataset(
                actor, dataset_id, "wrong", "forged"
            ),
        ):
            with pytest.raises(NotFoundError, match="^Dataset not found$"):
                call()
    assert snapshot(c) == before


@pytest.mark.parametrize("mutation", ["rename", "rename_back", "description", "field", "record"])
def test_stale_confirmation_cannot_delete(context, mutation):  # noqa: F811
    c = context
    populate(c)
    target = deletion(c)
    if mutation in {"rename", "rename_back", "description"}:
        c.s.datasets.update_dataset(c.a.id, c.d.key, "Changed", "Changed")
        if mutation == "rename_back":
            c.s.datasets.update_dataset(c.a.id, c.d.key, target.name, "")
    elif mutation == "field":
        c.s.dataset_fields.create_field(c.a.id, c.d.key, "extra", "Extra", "string")
    else:
        c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "new"})
    before = snapshot(c)
    with pytest.raises(ConflictError):
        remove(c, target)
    assert snapshot(c) == before


@pytest.mark.parametrize("stage", ["before", "child", "after_delete", "audit", "commit"])
def test_database_failure_rolls_back_every_row(context, monkeypatch, stage):  # noqa: F811
    c = context
    populate(c)
    target = deletion(c)
    before = snapshot(c)

    def fail(*args, **kwargs):
        raise OperationalError("private SQL", {}, Exception("private-path"))

    def sql_fail(connection, cursor, statement, parameters, execution_context, executemany):
        if statement.startswith("DELETE FROM dataset_records"):
            raise IntegrityError("private SQL", {}, Exception("constraint"))

    with monkeypatch.context() as patch:
        if stage == "before":
            patch.setattr(SqlAlchemyDatasetOperations, "deletion_counts", fail)
        elif stage == "child":
            event.listen(c.engine, "before_cursor_execute", sql_fail)
        elif stage == "after_delete":
            patch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
        elif stage == "audit":
            original = SqlAlchemyUnitOfWork.add_audit

            def duplicate(self, **values):
                original(self, **values)
                self.session.add(AuditEvent(id=audits(c)[0].id, **values))

            patch.setattr(SqlAlchemyUnitOfWork, "add_audit", duplicate)
        else:

            def commit(self):
                self.flush()
                fail()

            patch.setattr(Session, "commit", commit)
        try:
            with pytest.raises(PersistenceConflictError) as error:
                remove(c, target)
            assert "private" not in str(error.value)
        finally:
            if stage == "child":
                event.remove(c.engine, "before_cursor_execute", sql_fail)
    assert snapshot(c) == before
    remove(c, target)
    assert len([e for e in audits(c) if e.action == "dataset.deleted"]) == 1


def test_concurrent_deletes_and_replay_commit_one_event(context):  # noqa: F811
    c = context
    populate(c)
    target = deletion(c)
    gate = Barrier(2)

    def call():
        gate.wait(timeout=10)
        try:
            remove(c, target)
            return "deleted"
        except NotFoundError:
            return "missing"

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(call) for _ in range(2)]
        assert sorted(t.result(timeout=20) for t in tasks) == ["deleted", "missing"]
    before = snapshot(c)
    with pytest.raises(NotFoundError):
        remove(c, target)
    assert snapshot(c) == before
    assert len([e for e in audits(c) if e.action == "dataset.deleted"]) == 1


def stage_import(c):
    state, _, _ = c.s.record_imports.begin(
        c.a.id, "session", c.d.key, "input.csv", b"code,label\nnew,x\n"
    )
    assert c.s.record_imports.preview(
        c.a.id, "session", c.d.key, state.token, ("code", "label")
    ).valid
    return state


@pytest.mark.parametrize("operation", ["metadata", "field", "record", "import"])
def test_delete_serializes_with_mutation(context, monkeypatch, operation):  # noqa: F811
    c = context
    populate(c)
    target = deletion(c)
    staged = stage_import(c) if operation == "import" else None
    held, release, started = Event(), Event(), Event()
    original = SqlAlchemyDatasetOperations.delete_with_dependents

    def hold(self, dataset_id):
        held.set()
        assert release.wait(timeout=15)
        return original(self, dataset_id)

    monkeypatch.setattr(SqlAlchemyDatasetOperations, "delete_with_dependents", hold)

    def mutate():
        started.set()
        if operation == "metadata":
            return c.s.datasets.update_dataset(c.a.id, c.d.key, "Changed", "")
        if operation == "field":
            return c.s.dataset_fields.create_field(c.a.id, c.d.key, "extra", "Extra", "string")
        if operation == "record":
            return c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "new"})
        return c.s.record_imports.confirm(c.a.id, "session", c.d.key, staged.token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(remove, c, target)
        assert held.wait(timeout=10)
        mutating = pool.submit(mutate)
        try:
            assert started.wait(timeout=10)
        finally:
            release.set()
        deleting.result(timeout=20)
        with pytest.raises(NotFoundError):
            mutating.result(timeout=20)
    assert len([e for e in audits(c) if e.action == "dataset.deleted"]) == 1
    assert snapshot(c)["dataset_records"] == []


def test_import_claim_cannot_follow_recreated_key(context, monkeypatch):  # noqa: F811
    c = context
    populate(c)
    staged = stage_import(c)
    original = c.s.record_imports.store.claim

    def replace_target(state):
        claimed = original(state)
        remove(c)
        c.s.datasets.create_dataset(c.a.id, c.d.key, c.d.name, "", c.owner.id)
        return claimed

    monkeypatch.setattr(c.s.record_imports.store, "claim", replace_target)
    with pytest.raises(NotFoundError):
        c.s.record_imports.confirm(c.a.id, "session", c.d.key, staged.token)
    assert snapshot(c)["dataset_records"] == []
    assert not any(e.action == "dataset.records_imported" for e in audits(c))
    with pytest.raises(NotFoundError):
        c.s.record_imports.confirm(c.a.id, "session", c.d.key, staged.token)


def test_pending_import_expires_and_export_snapshot_survives(context):  # noqa: F811
    c = context
    populate(c)
    staged = stage_import(c)
    artifact = c.s.dataset_records.export_dataset(c.a.id, c.d.key, "csv", prefer_concept=True)
    remove(c)
    with pytest.raises(NotFoundError):
        c.s.record_imports.confirm(c.a.id, "session", c.d.key, staged.token)
    with pytest.raises(NotFoundError):
        c.s.dataset_records.export_dataset(c.a.id, c.d.key, "csv", prefer_concept=True)
    try:
        assert b"private-value" in artifact.content.read()
    finally:
        artifact.content.close()
    c.s.record_imports.store.clock = lambda: staged.expires_at + 1
    with pytest.raises(NotFoundError):
        c.s.record_imports.store.get(staged.token, c.a.id, "session", c.d.id)
    assert staged.token not in c.s.record_imports.store._states


def test_deleted_name_snapshot_and_history_presentation(context):  # noqa: F811
    c = context
    name = "<script>alert(1)</script>" + "\u202e" + "😀" * 170
    c.s.datasets.update_dataset(c.a.id, c.d.key, name, "private-description")
    old = audits(c)[-1]
    remove(c)
    e = audits(c)[-1]
    expected = "".join(char for char in name if unicodedata.category(char) != "Cf")
    assert e.details == {"dataset_name": expected}
    service = AuditHistoryService(c.s.datasets.uow)
    detail = service.get_event(c.a.id, e.id)
    assert detail.object_status == "deleted"
    assert detail.object_label == expected + " (deleted)"
    assert detail.context == (("Dataset name at deletion", expected),)
    assert service.get_event(c.a.id, old.id).object_label == "Deleted object"
    assert next(i for i in service.list_history(c.a.id).items if i.id == e.id) == detail


@pytest.mark.parametrize("value", ["", "x" * 201, [], None, {"secret": "value"}])
def test_snapshot_is_centrally_bounded(value):
    with pytest.raises(AuditContractError):
        validate_event(
            actor_user_id=str(uuid4()),
            object_type="dataset",
            object_id=str(uuid4()),
            action="dataset.deleted",
            details={"dataset_name": value},
        )


def browser(c, actor=None):
    s = c.s
    app = create_flask_app(
        s.users,
        s.authentication,
        s.sessions,
        s.teams,
        s.memberships,
        authorization=s.authorization,
        datasets=s.datasets,
        dataset_fields=s.dataset_fields,
        dataset_business_keys=s.dataset_business_keys,
        dataset_records=s.dataset_records,
        record_imports=s.record_imports,
    )
    client = app.test_client()
    token, login = s.sessions.create(actor or c.a)
    client.set_cookie(SESSION_COOKIE, token, domain="localhost")
    return client, login.csrf_token


def test_web_csrf_exact_confirmation_prg_and_replay(context):  # noqa: F811
    c = context
    client, csrf = browser(c)
    path = f"/datasets/id/{c.d.id}/delete"
    target = deletion(c)
    data = dict(
        csrf_token=csrf,
        confirmation_name=target.name,
        expected_updated_at=target.expected_updated_at,
    )
    before = snapshot(c)
    assert client.get(path, base_url="https://localhost").status_code == 200
    assert snapshot(c) == before
    for changes, status in [
        ({"csrf_token": "wrong"}, 403),
        ({"confirmation_name": "wrong"}, 400),
        ({"expected_updated_at": "wrong"}, 409),
    ]:
        response = client.post(path, data=data | changes, base_url="https://localhost")
        assert response.status_code == status
        assert snapshot(c) == before
    response = client.post(
        path,
        data=MultiDict([*data.items(), ("confirmation_name", "wrong")]),
        base_url="https://localhost",
    )
    assert response.status_code == 400 and snapshot(c) == before
    response = client.post(path, data=data, base_url="https://localhost")
    assert response.status_code == 303 and response.location == "/datasets?deleted=1"
    success = client.get(response.location, base_url="https://localhost")
    assert b"Dataset deleted permanently." in success.data
    after = snapshot(c)
    assert client.get(response.location, base_url="https://localhost").status_code == 200
    assert client.get(path, base_url="https://localhost").status_code == 404
    assert client.post(path, data=data, base_url="https://localhost").status_code == 404
    assert snapshot(c) == after


def test_web_denied_targets_are_uniform(context):  # noqa: F811
    c = context
    client, csrf = browser(c, c.u)
    responses = []
    before = snapshot(c)
    for dataset_id in (c.d.id, str(uuid4())):
        path = f"/datasets/id/{dataset_id}/delete"
        responses.extend(
            [
                client.get(path, base_url="https://localhost"),
                client.post(path, data={"csrf_token": csrf}, base_url="https://localhost"),
            ]
        )
    assert {r.status_code for r in responses} == {404}
    assert len({r.data for r in responses}) == 1
    assert snapshot(c) == before


def test_dataset_foreign_key_inventory(context):  # noqa: F811
    schema = inspect(context.engine)
    direct = {
        table
        for table in schema.get_table_names()
        for fk in schema.get_foreign_keys(table)
        if fk["referred_table"] == "datasets"
    }
    assert direct == {
        "dataset_fields",
        "dataset_records",
        "dataset_export_access",
        "dataset_versions",
        "dataset_concepts",
    }
    assert all(fk["referred_table"] == "users" for fk in schema.get_foreign_keys("audit_events"))


def test_deletion_on_complete_migration_chain(audit_db):  # noqa: F811
    engine, _, _ = audit_db
    # Replace only this fixture's isolated, still-empty metadata-created schema.
    Base.metadata.drop_all(engine)
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/refrncr/migrations")
    )
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    c = context.__wrapped__(audit_db)
    populate(c)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0020_ref_162"
    with pytest.raises(IntegrityError), c.sessions.begin() as session:
        session.add(DatasetRecord(dataset_id=str(uuid4()), identifier_key=b"test", revision=1))
    # Field RESTRICT is effective on the real migrated schema too.
    with pytest.raises(IntegrityError), c.sessions.begin() as session:
        session.execute(text("DELETE FROM dataset_fields WHERE dataset_id=:id"), {"id": c.d.id})
    before = snapshot(c)
    remove(c)
    after = snapshot(c)
    for table in (
        "dataset_records",
        "dataset_record_values",
        "dataset_fields",
        "dataset_business_key_fields",
        "dataset_export_access",
        "datasets",
    ):
        assert after[table] == []
    assert set(before["audit_events"]) < set(after["audit_events"])
    assert len(after["audit_events"]) == len(before["audit_events"]) + 1


def test_legacy_grant_creation_cannot_outlive_dataset(context, monkeypatch):  # noqa: F811
    c = context
    other_admin = c.s.users.create_user(
        c.a.id, "second-admin@example.com", "Second admin", "valid password", role="admin"
    )
    target = deletion(c)
    held, release, started = Event(), Event(), Event()
    original = SqlAlchemyDatasetOperations.delete_with_dependents

    def hold(self, dataset_id):
        held.set()
        assert release.wait(timeout=15)
        original(self, dataset_id)

    monkeypatch.setattr(SqlAlchemyDatasetOperations, "delete_with_dependents", hold)

    def grant():
        started.set()
        return c.s.grants.create_grant(other_admin.id, "user", c.u.id, "dataset", c.d.id, "viewer")

    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(remove, c, target)
        assert held.wait(timeout=10)
        granting = pool.submit(grant)
        try:
            assert started.wait(timeout=10)
        finally:
            release.set()
        deleting.result(timeout=20)
        with pytest.raises(NotFoundError):
            granting.result(timeout=20)
    assert snapshot(c)["grants"] == []


def test_committed_mutation_makes_waiting_delete_stale(context, monkeypatch):  # noqa: F811
    c = context
    target = deletion(c)
    held, release, started = Event(), Event(), Event()
    original = SqlAlchemyUnitOfWork.add_audit

    def hold(self, **values):
        original(self, **values)
        if values["action"] == "metadata_updated":
            held.set()
            assert release.wait(timeout=15)

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", hold)

    def delete():
        started.set()
        remove(c, target)

    with ThreadPoolExecutor(max_workers=2) as pool:
        changing = pool.submit(c.s.datasets.update_dataset, c.a.id, c.d.key, "Changed", "")
        assert held.wait(timeout=10)
        deleting = pool.submit(delete)
        try:
            assert started.wait(timeout=10)
        finally:
            release.set()
        changing.result(timeout=20)
        with pytest.raises(ConflictError):
            deleting.result(timeout=20)
    assert c.s.datasets.get_dataset(c.a.id, c.d.key).name == "Changed"
    assert not any(e.action == "dataset.deleted" for e in audits(c))


def test_current_export_in_progress_retains_coherent_snapshot_during_delete(context, monkeypatch):  # noqa: F811
    from refrncr import dataset_export

    c = context
    populate(c)
    # REF-155 invalidates pending Concept exports. Published exports keep ADR-012's
    # snapshot contract even when the Dataset is deleted during preparation.
    from refrncr.models import utc_now

    c.s.dataset_versions.publish(
        c.a.id,
        c.d.id,
        utc_now().date(),
        "Release",
        c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key).revision,
    )
    target = deletion(c)
    reading, release, started = Event(), Event(), Event()
    original = dataset_export.generate_export

    def hold(*args, **kwargs):
        reading.set()
        assert release.wait(timeout=15)
        return original(*args, **kwargs)

    monkeypatch.setattr(dataset_export, "generate_export", hold)

    def delete():
        started.set()
        return remove(c, target)

    with ThreadPoolExecutor(max_workers=2) as pool:
        exporting = pool.submit(c.s.dataset_records.export_dataset, c.a.id, c.d.key, "csv")
        assert reading.wait(timeout=10)
        deleting = pool.submit(delete)
        try:
            assert started.wait(timeout=10)
        finally:
            release.set()
        artifact = exporting.result(timeout=20)
        deleting.result(timeout=20)
    try:
        assert artifact.content.read().count(b"private-value") == 2
    finally:
        artifact.content.close()
    assert len([e for e in audits(c) if e.action == "dataset.export_requested"]) == 1
    assert len([e for e in audits(c) if e.action == "dataset.deleted"]) == 1

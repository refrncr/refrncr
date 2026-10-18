"""REF-149 event evidence against SQLite and PostgreSQL, through real services."""

import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session
from support import audits, snapshot

from refrncr.audit import OPERATIONS, validate_event
from refrncr.models import AuditEvent, User
from refrncr.persistence import SqlAlchemyDatasetRecordOperations, SqlAlchemyUnitOfWork
from refrncr.ports import PersistenceConflictError
from refrncr.services import (
    ConflictError,
    NotFoundError,
    StaleDatasetOwnerError,
    StaleFieldOrderError,
    StaleRecordError,
    UserManagementError,
)
from refrncr.web import SESSION_COOKIE, create_flask_app

MUTATIONS = (
    "UserService.create_user",
    "UserService.change_email",
    "UserService.disable_user",
    "UserService.enable_user",
    "UserService.delete_user",
    "TeamService.create_team",
    "TeamService.rename_team",
    "TeamService.delete_team",
    "MembershipService.create_membership",
    "MembershipService.delete_membership",
    "GrantService.create_grant",
    "GrantService.change_permission",
    "GrantService.delete_grant",
    "DatasetVersionService.publish",
    "DatasetVersionService.cancel",
    "DatasetService.create_dataset",
    "DatasetService.delete_dataset",
    "DatasetService.change_owner",
    "DatasetService.update_dataset",
    "DatasetService.grant_export_access",
    "DatasetService.revoke_export_access",
    "DatasetFieldService.create_field",
    "DatasetFieldService.update_field",
    "DatasetFieldService.update_field_and_business_key_membership",
    "DatasetFieldService.delete_field",
    "DatasetFieldService.replace_order",
    "DatasetFieldService.move_field",
    "DatasetBusinessKeyService.set_business_key",
    "DatasetRecordService.create_record",
    "DatasetRecordService.update_record",
    "DatasetRecordService.delete_record",
    "DatasetRecordService.restore_record",
    "DatasetRecordImportService.confirm",
)


def prepare(c, operation):
    """Return a concrete operation and its independently expected event/target."""
    s, a, u, d = c.s, c.a, c.u, c.d
    cls, method = operation.split(".")
    if cls == "DatasetVersionService":
        from datetime import timedelta

        from refrncr.models import utc_now

        s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True, True, True)
        revision = s.datasets.get_dataset(a.id, d.key).revision
        day = utc_now().date() + timedelta(days=1)
        if method == "publish":
            return lambda actor: s.dataset_versions.publish(actor, d.id, day, "", revision), [
                ("dataset", "dataset.version_published", d.id)
            ]
        v = s.dataset_versions.publish(a.id, d.id, day, "", revision)
        return lambda actor: s.dataset_versions.cancel(actor, d.id, v.id), [
            ("dataset", "dataset.version_cancelled", d.id)
        ]
    if cls == "UserService":
        if method == "create_user":
            return lambda actor: s.users.create_user(
                actor, "new@example.com", "New", "valid password"
            ), [("user", "created", None)]
        if method == "enable_user":
            s.users.disable_user(a.id, u.id)
        if method == "change_email":
            return lambda actor: s.users.change_email(actor, u.id, "new@example.com"), [
                ("user", "email_changed", u.id)
            ]
        action = {"disable_user": "disabled", "enable_user": "enabled", "delete_user": "deleted"}[
            method
        ]
        return lambda actor: getattr(s.users, method)(actor, u.id), [("user", action, u.id)]
    if cls == "TeamService":
        if method == "create_team":
            return lambda actor: s.teams.create_team(actor, "New"), [("team", "created", None)]
        if method == "rename_team":
            return lambda actor: s.teams.rename_team(actor, c.other.id, "New"), [
                ("team", "renamed", c.other.id)
            ]
        return lambda actor: s.teams.delete_team(actor, c.other.id), [
            ("team", "deleted", c.other.id)
        ]
    if cls == "MembershipService":
        if method == "create_membership":
            return lambda actor: s.memberships.create_membership(actor, u.id, c.owner.id), [
                ("membership", "created", None)
            ]
        m = s.memberships.create_membership(a.id, u.id, c.other.id)
        return lambda actor: s.memberships.delete_membership(actor, c.other.id, m.id), [
            ("membership", "deleted", m.id)
        ]
    if cls == "GrantService":
        if method == "create_grant":
            return lambda actor: s.grants.create_grant(
                actor, "user", u.id, "document", "one", "viewer"
            ), [("grant", "created", None)]
        g = s.grants.create_grant(a.id, "user", u.id, "document", "one", "viewer")
        if method == "change_permission":
            return lambda actor: s.grants.change_permission(actor, g.id, "editor"), [
                ("grant", "permission_changed", g.id)
            ]
        return lambda actor: s.grants.delete_grant(actor, g.id), [("grant", "deleted", g.id)]
    if cls == "DatasetService":
        if method == "delete_dataset":
            target = s.datasets.get_dataset_for_deletion(a.id, d.id)
            return lambda actor: s.datasets.delete_dataset(
                actor, d.id, target.name, target.expected_updated_at
            ), [("dataset", "dataset.deleted", d.id)]
        if method == "create_dataset":
            return lambda actor: s.datasets.create_dataset(
                actor, "new", "New", "Secret", c.owner.id
            ), [("dataset", "created", None), ("dataset", "dataset.concept_created", None)]
        if method == "change_owner":
            return lambda actor: s.datasets.change_owner(actor, d.key, c.owner.id, c.other.id), [
                ("dataset", "owner_changed", d.id)
            ]
        if method == "update_dataset":
            return lambda actor: s.datasets.update_dataset(actor, d.key, "Changed", "secret-new"), [
                ("dataset", "metadata_updated", d.id)
            ]
        if method == "grant_export_access":
            return lambda actor: s.datasets.grant_export_access(actor, d.key, "user", u.id), [
                ("dataset_export_access", "granted", None)
            ]
        access = s.datasets.grant_export_access(a.id, d.key, "user", u.id)
        return lambda actor: s.datasets.revoke_export_access(actor, d.key, access.id), [
            ("dataset_export_access", "revoked", access.id)
        ]
    if cls == "DatasetFieldService":
        if method == "create_field":
            return lambda actor: s.dataset_fields.create_field(
                actor, d.key, "code", "Code", "string"
            ), [("dataset_field", "created", None)]
        f = s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True)
        if method in ("replace_order", "move_field"):
            g = s.dataset_fields.create_field(a.id, d.key, "label", "Label", "string")
            if method == "move_field":

                def call(actor):
                    return s.dataset_fields.move_field(actor, d.key, f.id, "down", (f.id, g.id))
            else:

                def call(actor):
                    return s.dataset_fields.replace_order(actor, d.key, (f.id, g.id), (g.id, f.id))

            return call, [("dataset", "field_order_updated", d.id)]
        if method == "delete_field":
            return lambda actor: s.dataset_fields.delete_field(actor, d.key, f.key), [
                ("dataset_field", "deleted", f.id)
            ]
        if method == "update_field_and_business_key_membership":
            return lambda actor: s.dataset_fields.update_field_and_business_key_membership(
                actor, d.key, f.key, "Changed", "string", True, False, True
            ), [
                ("dataset", "business_key_defined", d.id),
                ("dataset_field", "definition_updated", f.id),
            ]
        return lambda actor: s.dataset_fields.update_field(
            actor, d.key, f.key, "Changed", "string", True, False
        ), [("dataset_field", "definition_updated", f.id)]
    s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True)
    if cls == "DatasetBusinessKeyService":
        return lambda actor: s.dataset_business_keys.set_business_key(actor, d.key, ("code",)), [
            ("dataset", "business_key_defined", d.id)
        ]
    s.dataset_business_keys.set_business_key(a.id, d.key, ("code",))
    if cls == "DatasetRecordImportService":
        state, _, _ = s.record_imports.begin(
            a.id, "trusted-session", d.key, "records.csv", b"code,label\nsecret-a,x\nsecret-b,y\n"
        )
        s.record_imports.preview(a.id, "trusted-session", d.key, state.token, ("code", None))
        return lambda actor: s.record_imports.confirm(
            actor, "trusted-session", d.key, state.token
        ), [("dataset", "dataset.records_imported", d.id)]
    if method == "create_record":
        return lambda actor: s.dataset_records.create_record(
            actor, d.key, {"code": "secret-value"}
        ), [("dataset_record", "created", None)]
    r = s.dataset_records.create_record(a.id, d.key, {"code": "secret-old"})
    if method == "restore_record":
        s.dataset_records.delete_record(a.id, d.key, r.id, 1)
        with s.datasets.uow() as work:
            concept = work.concepts.get(d.id)
        return lambda actor: s.dataset_records.restore_record(
            actor, d.key, r.id, 2, concept_id=concept.id
        ), [("dataset_record", "restored", r.id)]
    if method == "update_record":
        return lambda actor: s.dataset_records.update_record(
            actor, d.key, r.id, 1, {"code": "secret-new"}
        ), [("dataset_record", "updated", r.id)]
    return lambda actor: s.dataset_records.delete_record(actor, d.key, r.id, 1), [
        ("dataset_record", "deleted", r.id)
    ]


@pytest.mark.parametrize("operation", MUTATIONS)
def test_declared_event_actor_target_and_safe_context(database_context, operation):
    c = database_context
    call, expected = prepare(c, operation)
    before = {event.id for event in audits(c)}
    result = call(c.a.id)
    events = [e for e in audits(c) if e.id not in before]
    actual = {(e.object_type, e.action, e.object_id) for e in events}
    expected = {(kind, action, target or result.id) for kind, action, target in expected}
    assert actual == expected and len(events) == len(expected)
    module = (
        "record_import"
        if operation.startswith("DatasetRecordImportService")
        else "dataset_versions"
        if operation.startswith("DatasetVersionService")
        else "services"
    )
    declared = OPERATIONS[f"refrncr.{module}.{operation}"].events
    for e in events:
        assert e.actor_user_id == c.a.id and e.timestamp is not None
        assert (e.object_type, e.action) in declared
        assert (
            validate_event(
                actor_user_id=e.actor_user_id,
                object_type=e.object_type,
                object_id=e.object_id,
                action=e.action,
                details=e.details,
            )
            == e.details
        )
        assert "secret" not in json.dumps(e.details).lower()
    if operation.endswith(".confirm"):
        assert events[0].details == {"format": "CSV", "record_count": 2, "rejected_row_count": 0}


@pytest.mark.parametrize("operation", MUTATIONS)
@pytest.mark.parametrize(
    "failure", ["after_business_flush", "after_audit_flush", "before_commit", "database_constraint"]
)
def test_every_mutation_rolls_back_business_and_audit(
    database_context, monkeypatch, operation, failure
):
    c = database_context
    call, _ = prepare(c, operation)
    before = snapshot(c)
    original = SqlAlchemyUnitOfWork.add_audit
    existing_event_id = audits(c)[0].id

    def fail(self, **values):
        if failure == "database_constraint":
            self.session.add(AuditEvent(id=existing_event_id, **values))
            self.flush()  # A real primary-key failure, translated by the persistence adapter.
        if failure == "after_audit_flush":
            original(self, **values)
        self.flush()
        raise RuntimeError("injected transaction failure")

    def fail_commit(self):
        self.flush()
        raise RuntimeError("injected transaction failure")

    with monkeypatch.context() as patch:
        if failure == "before_commit":
            patch.setattr(Session, "commit", fail_commit)
        else:
            patch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
        expected_error = (
            PersistenceConflictError if failure == "database_constraint" else RuntimeError
        )
        if operation.startswith("DatasetVersionService") and failure == "database_constraint":
            expected_error = ConflictError
        with pytest.raises(expected_error):
            call(c.a.id)
    assert snapshot(c) == before
    if operation == "DatasetRecordImportService.confirm":
        previous_ids = {e.id for e in audits(c)}
        assert call(c.a.id) == 2  # Rollback must release the transient claim for retry.
        events = [e for e in audits(c) if e.id not in previous_ids]
        assert len(events) == 1 and events[0].action == "dataset.records_imported"


@pytest.mark.parametrize("operation", MUTATIONS)
def test_every_mutation_denies_untrusted_or_missing_actor(database_context, operation):
    c = database_context
    call, _ = prepare(c, operation)
    before = snapshot(c)
    for actor in (str(uuid.uuid4()), c.u.id):
        with pytest.raises(UserManagementError):
            call(actor)
        assert snapshot(c) == before


@pytest.mark.parametrize("operation", MUTATIONS)
def test_repeated_submission_cannot_add_event_without_change(database_context, operation):
    c = database_context
    call, _ = prepare(c, operation)
    call(c.a.id)
    before = snapshot(c)
    try:
        call(c.a.id)
    except UserManagementError:
        pass
    assert snapshot(c) == before


def test_initial_admin_success_failure_and_duplicate(audit_db, monkeypatch):
    _engine, sessions, s = audit_db
    s.users.bootstrap_organisation()
    original = SqlAlchemyUnitOfWork.add_audit

    def fail(self, **values):
        original(self, **values)
        self.flush()
        raise RuntimeError("audit failed")

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
        with pytest.raises(RuntimeError):
            s.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    with sessions() as session:
        assert list(session.scalars(select(User))) == []
        assert list(session.scalars(select(AuditEvent))) == []
    admin = s.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    with pytest.raises(ConflictError):
        s.users.create_initial_admin("admin@example.com", "Admin", "valid password")
    with sessions() as session:
        event = session.scalars(select(AuditEvent)).one()
        assert (event.actor_user_id, event.object_id, event.object_type, event.action) == (
            admin.id,
            admin.id,
            "user",
            "created",
        )


def test_all_normalized_noops_preserve_timestamps_revisions_and_events(database_context):
    c = database_context
    s, a, u, d = c.s, c.a, c.u, c.d
    s.teams.rename_team(a.id, c.other.id, "Café")
    grant = s.grants.create_grant(a.id, "user", u.id, "document", "one", "viewer")
    field = s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True)
    s.dataset_business_keys.set_business_key(a.id, d.key, ("code",))
    record = s.dataset_records.create_record(a.id, d.key, {"code": "Café"})
    before = snapshot(c)
    s.users.change_email(a.id, u.id, " USER@EXAMPLE.COM ")
    s.users.enable_user(a.id, u.id)
    s.teams.rename_team(a.id, c.other.id, " Cafe\u0301 ")
    s.grants.change_permission(a.id, grant.id, "viewer")
    s.datasets.update_dataset(a.id, d.key, " Business name ", d.description)
    s.datasets.change_owner(a.id, d.key, c.owner.id, c.owner.id)
    s.dataset_fields.update_field(a.id, d.key, field.key, " Code ", "string", True, False)
    s.dataset_fields.update_field_and_business_key_membership(
        a.id, d.key, field.key, " Code ", "string", True, False, True
    )
    s.dataset_fields.move_field(a.id, d.key, field.id, "up", (field.id,))
    s.dataset_business_keys.set_business_key(a.id, d.key, ("code",))
    result = s.dataset_records.update_record(a.id, d.key, record.id, 1, {"code": " Cafe\u0301 "})
    assert result.revision == 1
    assert snapshot(c) == before
    s.users.disable_user(a.id, u.id)
    before = snapshot(c)
    s.users.disable_user(a.id, u.id)
    assert snapshot(c) == before
    s.users.delete_user(a.id, u.id)
    before = snapshot(c)
    s.users.delete_user(a.id, u.id)
    assert snapshot(c) == before


def test_stale_same_value_is_rejected_before_noop(database_context):
    c = database_context
    s, a, d = c.s, c.a, c.d
    f = s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True)
    s.dataset_business_keys.set_business_key(a.id, d.key, ("code",))
    r = s.dataset_records.create_record(a.id, d.key, {"code": "old"})
    s.dataset_records.update_record(a.id, d.key, r.id, 1, {"code": "new"})
    s.datasets.change_owner(a.id, d.key, c.owner.id, c.other.id)
    before = snapshot(c)
    with pytest.raises(StaleRecordError):
        s.dataset_records.update_record(a.id, d.key, r.id, 1, {"code": "new"})
    with pytest.raises(StaleDatasetOwnerError):
        s.datasets.change_owner(a.id, d.key, c.owner.id, c.other.id)
    with pytest.raises(StaleFieldOrderError):
        s.dataset_fields.replace_order(a.id, d.key, (), (f.id,))
    assert snapshot(c) == before


@pytest.mark.parametrize("format_name", ["csv", "xlsx", "json"])
def test_export_prepared_then_audit_committed_before_return(
    database_context, monkeypatch, format_name
):
    c = database_context
    original = SqlAlchemyUnitOfWork.__exit__
    stages = []

    def exit_uow(self, *args):
        value = original(self, *args)
        stages.append("write_closed" if self.write else "read_closed")
        return value

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "__exit__", exit_uow)
    before = {e.id for e in audits(c)}
    artifact = c.s.dataset_records.export_dataset(c.a.id, c.d.key, format_name, prefer_concept=True)
    try:
        assert stages == ["read_closed", "write_closed"]
        events = [e for e in audits(c) if e.id not in before]
        assert len(events) == 1
        e = events[0]
        assert (e.action, e.object_type, e.object_id, e.actor_user_id) == (
            "dataset.export_requested",
            "dataset",
            c.d.id,
            c.a.id,
        )
        assert e.details == {"format": {"csv": "CSV", "xlsx": "Excel", "json": "JSON"}[format_name]}
        assert artifact.content.read()
    finally:
        artifact.content.close()


@pytest.mark.parametrize(
    "failure", ["generation", "readability", "snapshot_close", "audit_insert", "audit_commit"]
)
def test_export_preparation_or_audit_failure_closes_artifact_without_event(
    database_context, monkeypatch, failure
):
    c = database_context
    from refrncr import dataset_export

    before = snapshot(c)
    artifacts = []
    original = dataset_export.generate_export

    def generate(*args):
        if failure == "generation":
            raise RuntimeError("generation failure")
        artifact = original(*args)
        artifacts.append(artifact)
        return artifact

    def fail(*args, **kwargs):
        raise RuntimeError("injected failure")

    original_exit = SqlAlchemyUnitOfWork.__exit__

    def fail_snapshot_exit(self, *args):
        result = original_exit(self, *args)
        if not self.write:
            raise RuntimeError("snapshot closure failed")
        return result

    monkeypatch.setattr(dataset_export, "generate_export", generate)
    if failure == "readability":
        monkeypatch.setattr(dataset_export, "verify_artifact_readable", fail)
    elif failure == "snapshot_close":
        monkeypatch.setattr(SqlAlchemyUnitOfWork, "__exit__", fail_snapshot_exit)
    elif failure == "audit_insert":
        monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
    elif failure == "audit_commit":
        monkeypatch.setattr(Session, "commit", fail)
    with pytest.raises(RuntimeError):
        c.s.dataset_records.export_dataset(c.a.id, c.d.key, "json", prefer_concept=True)
    assert all(artifact.content.closed for artifact in artifacts)
    assert snapshot(c) == before


def browser(c):
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
    return app.test_client()


def test_export_authentication_authorization_format_and_trusted_browser_actor(database_context):
    c = database_context
    client = browser(c)
    url = f"/datasets/{c.d.key}/concept/records/export"
    before = {e.id for e in audits(c)}
    assert client.get(url + "?format=json", base_url="https://localhost").status_code == 302
    for actor in (c.u, c.a):
        token, _ = c.s.sessions.create(actor)
        client.set_cookie(SESSION_COOKIE, token, domain="localhost", secure=True)
        for invalid in ("", "?format=pdf", "?format=csv&format=json"):
            assert client.get(url + invalid, base_url="https://localhost").status_code == (
                404 if actor == c.u else 400
            )
        if actor == c.u:
            assert client.get(url + "?format=json", base_url="https://localhost").status_code == 404
    assert {e.id for e in audits(c)} == before
    response = client.get(url + "?format=json&actor_id=" + c.u.id, base_url="https://localhost")
    assert response.status_code == 200
    response.close()
    event = [e for e in audits(c) if e.id not in before][0]
    assert event.actor_user_id == c.a.id


def test_post_stream_failure_retains_one_request_event_and_logs_safely(
    database_context, monkeypatch, caplog
):
    c = database_context
    client = browser(c)
    token, _ = c.s.sessions.create(c.a)
    client.set_cookie(SESSION_COOKIE, token, domain="localhost", secure=True)
    from refrncr import web

    original = web.send_file

    def send(*args, **kwargs):
        response = original(*args, **kwargs)
        underlying = response.response

        def broken():
            try:
                yield b"["
                raise OSError("secret payload path /private/data.csv")
            finally:
                underlying.close()

        response.response = broken()
        return response

    monkeypatch.setattr(web, "send_file", send)
    before = {e.id for e in audits(c)}
    response = client.get(
        f"/datasets/{c.d.key}/concept/records/export?format=json",
        base_url="https://localhost",
        buffered=False,
    )
    iterator = iter(response.response)
    assert next(iterator) == b"["
    with pytest.raises(OSError):
        next(iterator)
    response.close()
    events = [e for e in audits(c) if e.id not in before]
    assert len(events) == 1 and events[0].action == "dataset.export_requested"
    assert "Dataset export stream failed" in caplog.text
    assert "secret payload" not in caplog.text and "/private/data.csv" not in caplog.text


@pytest.mark.parametrize("format_name", ["csv", "xlsx"])
def test_bulk_import_exactly_one_summary_and_cancel_or_failure_none(database_context, format_name):
    c = database_context
    s, a, d = c.s, c.a, c.d
    s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True)
    s.dataset_business_keys.set_business_key(a.id, d.key, ("code",))
    if format_name == "csv":
        content = b"code,ignored\nsecret-a,x\nsecret-b,y\n"
    else:
        book = Workbook()
        book.active.append(["code", "ignored"])
        book.active.append(["secret-a", "x"])
        book.active.append(["secret-b", "y"])
        buffer = io.BytesIO()
        book.save(buffer)
        book.close()
        content = buffer.getvalue()
    importer = s.record_imports
    state, _, _ = importer.begin(a.id, "trusted", d.key, "private." + format_name, content)
    importer.preview(a.id, "trusted", d.key, state.token, ("code", None))
    before = snapshot(c)
    importer.cancel(a.id, "trusted", d.key, state.token)
    with pytest.raises(NotFoundError):
        importer.confirm(a.id, "trusted", d.key, state.token)
    assert snapshot(c) == before
    state, _, _ = importer.begin(a.id, "trusted", d.key, "private." + format_name, content)
    importer.preview(a.id, "trusted", d.key, state.token, ("code", None))
    before_ids = {e.id for e in audits(c)}
    assert importer.confirm(a.id, "trusted", d.key, state.token) == 2
    events = [e for e in audits(c) if e.id not in before_ids]
    assert len(events) == 1
    assert events[0].details == {
        "format": "CSV" if format_name == "csv" else "Excel",
        "record_count": 2,
        "rejected_row_count": 0,
    }
    with pytest.raises(NotFoundError):
        importer.confirm(a.id, "trusted", d.key, state.token)
    state, _, _ = importer.begin(a.id, "trusted", d.key, "private." + format_name, content)
    assert not importer.preview(a.id, "trusted", d.key, state.token, ("code", None)).valid
    before = snapshot(c)
    with pytest.raises(UserManagementError):
        importer.confirm(a.id, "trusted", d.key, state.token)
    assert snapshot(c) == before


def test_concurrent_same_email_has_one_committed_change(database_context):
    c = database_context
    barrier = Barrier(2)
    before = {e.id for e in audits(c)}

    def change():
        barrier.wait(timeout=10)
        c.s.users.change_email(c.a.id, c.u.id, "same@example.com")

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(change) for _ in range(2)]
        for task in tasks:
            task.result(timeout=15)
    events = [e for e in audits(c) if e.id not in before]
    assert len(events) == 1 and events[0].action == "email_changed"


def test_export_snapshot_remains_coherent_during_writes(database_context, monkeypatch):
    c = database_context
    if c.engine.dialect.name == "sqlite":
        with c.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    s, a, d = c.s, c.a, c.d
    s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True)
    s.dataset_business_keys.set_business_key(a.id, d.key, ("code",))
    record = s.dataset_records.create_record(a.id, d.key, {"code": "old"})
    ready, release = Event(), Event()
    original = SqlAlchemyDatasetRecordOperations.iterate_all

    def paused(self, dataset_id):
        iterator = original(self, dataset_id)
        first = next(iterator)
        ready.set()
        assert release.wait(timeout=10)
        yield first
        yield from iterator

    monkeypatch.setattr(SqlAlchemyDatasetRecordOperations, "iterate_all", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            s.dataset_records.export_dataset, a.id, d.key, "json", prefer_concept=True
        )
        assert ready.wait(timeout=10)
        try:
            s.dataset_records.update_record(a.id, d.key, record.id, 1, {"code": "new"})
        finally:
            release.set()
        artifact = pending.result(timeout=15)
    try:
        assert json.load(artifact.content) == [{"code": "old"}]
    finally:
        artifact.content.close()
    assert sum(e.action == "dataset.export_requested" for e in audits(c)) == 1


@pytest.mark.parametrize("operation", MUTATIONS)
def test_invalid_input_or_lifecycle_never_creates_success_event(database_context, operation):
    c = database_context
    s, a, u, d = c.s, c.a, c.u, c.d
    _call, expected = prepare(c, operation)
    target = expected[-1][2]
    missing = str(uuid.uuid4())
    if operation == "UserService.enable_user":
        s.users.delete_user(a.id, u.id)
    if operation == "MembershipService.create_membership":
        s.users.disable_user(a.id, u.id)
    invalid = {
        "DatasetVersionService.publish": lambda: s.dataset_versions.publish(
            a.id, d.id, None, "", -1
        ),
        "DatasetVersionService.cancel": lambda: s.dataset_versions.cancel(a.id, d.id, missing),
        "UserService.create_user": lambda: s.users.create_user(
            a.id, "invalid", "Name", "valid password"
        ),
        "UserService.change_email": lambda: s.users.change_email(a.id, u.id, "invalid"),
        "UserService.disable_user": lambda: s.users.disable_user(a.id, a.id),
        "UserService.enable_user": lambda: s.users.enable_user(a.id, u.id),
        "UserService.delete_user": lambda: s.users.delete_user(a.id, a.id),
        "TeamService.create_team": lambda: s.teams.create_team(a.id, ""),
        "TeamService.rename_team": lambda: s.teams.rename_team(a.id, c.other.id, ""),
        "TeamService.delete_team": lambda: s.teams.delete_team(a.id, c.owner.id),
        "MembershipService.create_membership": lambda: s.memberships.create_membership(
            a.id, u.id, c.owner.id
        ),
        "MembershipService.delete_membership": lambda: s.memberships.delete_membership(
            a.id, c.owner.id, target
        ),
        "GrantService.create_grant": lambda: s.grants.create_grant(
            a.id, "user", u.id, "document", "one", "invalid"
        ),
        "GrantService.change_permission": lambda: s.grants.change_permission(
            a.id, target, "invalid"
        ),
        "GrantService.delete_grant": lambda: s.grants.delete_grant(a.id, missing),
        "DatasetService.create_dataset": lambda: s.datasets.create_dataset(
            a.id, "Invalid Key", "Name", "", c.owner.id
        ),
        "DatasetService.delete_dataset": lambda: s.datasets.delete_dataset(
            a.id,
            d.id,
            "Wrong name",
            s.datasets.get_dataset_for_deletion(a.id, d.id).expected_updated_at,
        ),
        "DatasetService.change_owner": lambda: s.datasets.change_owner(
            a.id, d.key, c.owner.id, missing
        ),
        "DatasetService.update_dataset": lambda: s.datasets.update_dataset(a.id, d.key, "", ""),
        "DatasetService.grant_export_access": lambda: s.datasets.grant_export_access(
            a.id, d.key, "group", u.id
        ),
        "DatasetService.revoke_export_access": lambda: s.datasets.revoke_export_access(
            a.id, d.key, missing
        ),
        "DatasetFieldService.create_field": lambda: s.dataset_fields.create_field(
            a.id, d.key, "bad key", "Name", "string"
        ),
        "DatasetFieldService.update_field": lambda: s.dataset_fields.update_field(
            a.id, d.key, "code", "", "string", True, False
        ),
        "DatasetFieldService.update_field_and_business_key_membership": lambda: (
            s.dataset_fields.update_field_and_business_key_membership(
                a.id, d.key, "code", "Code", "string", False, False, True
            )
        ),
        "DatasetFieldService.delete_field": lambda: s.dataset_fields.delete_field(
            a.id, d.key, "missing"
        ),
        "DatasetFieldService.replace_order": lambda: s.dataset_fields.replace_order(
            a.id, d.key, (), (missing,)
        ),
        "DatasetFieldService.move_field": lambda: s.dataset_fields.move_field(
            a.id, d.key, missing, "sideways", ()
        ),
        "DatasetBusinessKeyService.set_business_key": lambda: (
            s.dataset_business_keys.set_business_key(a.id, d.key, ())
        ),
        "DatasetRecordService.create_record": lambda: s.dataset_records.create_record(
            a.id, d.key, {"unknown": "secret"}
        ),
        "DatasetRecordService.update_record": lambda: s.dataset_records.update_record(
            a.id, d.key, target, 1, {"code": ""}
        ),
        "DatasetRecordService.restore_record": lambda: s.dataset_records.restore_record(
            a.id, d.key, target, 0, concept_id=missing
        ),
        "DatasetRecordService.delete_record": lambda: s.dataset_records.delete_record(
            a.id, d.key, target, 0
        ),
        "DatasetRecordImportService.confirm": lambda: s.record_imports.confirm(
            a.id, "trusted-session", d.key, "invalid-token"
        ),
    }
    before = snapshot(c)
    with pytest.raises(UserManagementError):
        invalid[operation]()
    assert snapshot(c) == before


def test_composite_events_and_team_cascade_contract(database_context):
    c = database_context
    s, a, d = c.s, c.a, c.d
    before = {e.id for e in audits(c)}
    f = s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True, False, True)
    emitted = [e for e in audits(c) if e.id not in before]
    assert {(e.object_type, e.object_id, e.action) for e in emitted} == {
        ("dataset_field", f.id, "created"),
        ("dataset", d.id, "business_key_defined"),
    }
    before = {e.id for e in audits(c)}
    s.dataset_fields.update_field_and_business_key_membership(
        a.id, d.key, "code", "Code", "string", True, False, False
    )
    emitted = [e for e in audits(c) if e.id not in before]
    assert len(emitted) == 1 and emitted[0].action == "business_key_updated"
    before = {e.id for e in audits(c)}
    s.datasets.update_dataset(
        a.id, d.key, "Changed", "secret", owner_change=(c.owner.id, c.other.id)
    )
    emitted = [e for e in audits(c) if e.id not in before]
    assert len(emitted) == 2
    assert {e.action for e in emitted} == {"metadata_updated", "owner_changed"}
    membership = s.memberships.create_membership(a.id, c.u.id, c.owner.id)
    grant = s.grants.create_grant(a.id, "team", c.owner.id, "document", "one", "viewer")
    access = s.datasets.grant_export_access(a.id, d.key, "team", c.owner.id)
    before = {e.id for e in audits(c)}
    s.teams.delete_team(a.id, c.owner.id)
    emitted = [e for e in audits(c) if e.id not in before]
    assert len(emitted) == 2
    assert {(e.object_type, e.object_id, e.action) for e in emitted} == {
        ("team", c.owner.id, "deleted"),
        ("grant", grant.id, "deleted"),
    }
    from refrncr.models import DatasetExportAccess, Membership

    with c.sessions() as session:
        assert session.get(Membership, membership.id) is None
        assert session.get(DatasetExportAccess, access.id) is None


def test_ordinary_service_reads_and_preview_do_not_write_domain_history(database_context):
    c = database_context
    s, a, d = c.s, c.a, c.d
    s.dataset_fields.create_field(a.id, d.key, "code", "Code", "string", True)
    s.dataset_business_keys.set_business_key(a.id, d.key, ("code",))
    record = s.dataset_records.create_record(a.id, d.key, {"code": "existing"})
    before = {e.id for e in audits(c)}
    s.users.has_users()
    s.users.list_users(a.id)
    s.teams.list_teams(a.id)
    s.memberships.list_team_members(a.id, c.owner.id)
    s.memberships.list_eligible_users(a.id, c.owner.id)
    s.datasets.list_datasets(a.id)
    s.datasets.list_dataset_overview(a.id)
    s.datasets.get_dataset(a.id, d.key)
    s.datasets.get_dataset_context(a.id, d.key)
    s.datasets.get_dataset_details(a.id, d.key)
    s.datasets.get_dataset_for_edit(a.id, d.key)
    s.datasets.export_access_context(a.id, d.key)
    s.dataset_fields.list_fields(a.id, d.key)
    s.dataset_fields.get_field(a.id, d.key, "code")
    s.dataset_business_keys.get_business_key(a.id, d.key)
    s.dataset_records.get_dataset_for_add(a.id, d.key)
    s.dataset_records.list_records(a.id, d.key)
    s.dataset_records.get_record_for_edit(a.id, d.key, record.id)
    s.authorization.datasets.creation_teams(a.id)
    s.authorization.authorized_resources(a.id, "dataset", "viewer")
    state, _, _ = s.record_imports.begin(
        a.id, "trusted", d.key, "preview.csv", b"code,ignored\nnew,x\n"
    )
    s.record_imports.mapping_assistance(a.id, "trusted", d.key, state.token)
    assert s.record_imports.preview(a.id, "trusted", d.key, state.token, ("code", None)).valid
    s.record_imports.saved_preview(a.id, "trusted", d.key, state.token)
    s.record_imports.navigate(a.id, "trusted", d.key, state.token, 4)
    s.record_imports.state(a.id, "trusted", d.key, state.token)
    s.record_imports.active(a.id, "trusted", d.key)
    s.record_imports.cancel(a.id, "trusted", d.key, state.token)
    assert {e.id for e in audits(c)} == before


def test_browser_route_inventory_matches_real_registration(database_context):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((root / "docs/development/audit-inventory.json").read_text())
    expected = {}
    for row in inventory["routes"]:
        if not row["symbol"].startswith("refrncr.web."):
            continue
        methods = set(row["methods"]) | {"OPTIONS"}
        if "GET" in methods:
            methods.add("HEAD")
        expected[(row["symbol"].split(".")[-1], row["path"])] = methods
    actual = {
        (rule.endpoint, rule.rule): rule.methods
        for rule in browser(database_context).application.url_map.iter_rules()
        if rule.endpoint != "static"
    }
    assert actual == expected


def test_response_construction_failure_keeps_request_and_closes_artifact(
    database_context, monkeypatch
):
    from refrncr import dataset_export, web

    c = database_context
    client = browser(c)
    client.application.testing = True
    token, _ = c.s.sessions.create(c.a)
    client.set_cookie(SESSION_COOKIE, token, domain="localhost", secure=True)
    artifacts = []
    original = dataset_export.generate_export

    def generate(*args):
        artifact = original(*args)
        artifacts.append(artifact)
        return artifact

    def fail(*args, **kwargs):
        raise RuntimeError("response construction failed")

    monkeypatch.setattr(dataset_export, "generate_export", generate)
    monkeypatch.setattr(web, "send_file", fail)
    before = {e.id for e in audits(c)}
    response = client.get(
        f"/datasets/{c.d.key}/concept/records/export?format=json", base_url="https://localhost"
    )
    assert response.status_code == 500
    assert b"response construction" not in response.data
    assert len(artifacts) == 1 and artifacts[0].content.closed
    events = [e for e in audits(c) if e.id not in before]
    assert len(events) == 1 and events[0].action == "dataset.export_requested"


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS"])
def test_export_head_and_options_have_explicit_audit_semantics(database_context, method):
    c = database_context
    client = browser(c)
    token, _ = c.s.sessions.create(c.a)
    client.set_cookie(SESSION_COOKIE, token, domain="localhost", secure=True)
    before = {e.id for e in audits(c)}
    response = client.open(
        f"/datasets/{c.d.key}/concept/records/export?format=json",
        method=method,
        base_url="https://localhost",
    )
    assert response.status_code == 200
    if method == "HEAD":
        assert response.data == b""
    response.close()
    events = [e for e in audits(c) if e.id not in before]
    assert len(events) == (1 if method == "HEAD" else 0)
    if events:
        assert events[0].action == "dataset.export_requested"


@pytest.mark.parametrize("failure", ["read", "close", "read_and_close"])
def test_stream_read_and_cleanup_failures_log_once_without_payload(caplog, failure):
    from refrncr.dataset_export import logged_export_stream

    class BrokenStream:
        closed = False

        def __iter__(self):
            yield b"prepared content"
            if failure in {"read", "read_and_close"}:
                raise OSError("private-read-payload")

        def close(self):
            self.closed = True
            if failure in {"close", "read_and_close"}:
                raise OSError("private-close-payload")

    underlying = BrokenStream()
    stream = logged_export_stream(underlying)
    assert next(stream) == b"prepared content"
    with pytest.raises(OSError):
        next(stream)
    assert underlying.closed
    records = [r for r in caplog.records if r.name == "refrncr.dataset_export"]
    assert len(records) == 1
    assert records[0].getMessage() == "Dataset export stream failed"
    assert records[0].exc_info is None and "private-" not in caplog.text

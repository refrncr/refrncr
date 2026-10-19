"""REF-155 discard, Current eligibility and transaction/security contracts."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier, Event

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session
from support import NOW, active, audits, html_elements, publish, request, setup, snapshot
from support import dataset_version_client as client
from werkzeug.datastructures import MultiDict

from refrncr.concept_contracts import DISCARD_UNAVAILABLE
from refrncr.persistence import SqlAlchemyUnitOfWork
from refrncr.services import ConflictError, NotFoundError


def prepared(c, *, extra=False):
    setup(c)
    if extra:
        c.retained = c.s.dataset_records.create_record(
            c.a.id, c.d.key, {"code": "c", "label": "Before"}
        )
    current = publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    return current


def discard(c, actor=None, identity=None):
    actor = actor or c.a.id
    identity = identity or c.service.review_discard(actor, c.d.id).identity
    return c.service.discard_concept(actor, c.d.id, identity)


def form(browser, c, csrf):
    response = request(browser, "get", f"/datasets/{c.d.key}/concept/discard")
    assert response.status_code == 200
    token = re.search(r"name='confirmation' value='([^']+)'", response.text)[1]
    return {"csrf_token": csrf, "confirmation": token, "confirmed": "yes"}


@pytest.mark.parametrize("published", [False, True])
def test_current_required_preserves_first_metadata(context, published):
    c = context
    setup(c)
    if published:
        publish(c, day=NOW.date() + timedelta(days=1))
        c.service.create_concept(c.a.id, c.d.id)
    c.s.datasets.update_dataset(c.a.id, c.d.key, "First unpublished name", "Keep description")
    browser, csrf = client(c)
    before = snapshot(c)
    for operation in (
        lambda: c.service.review_discard(c.a.id, c.d.id),
        lambda: c.service.discard_concept(c.a.id, c.d.id, None),
    ):
        with pytest.raises(ConflictError, match="There is no Current"):
            operation()
    response = request(browser, "get", f"/datasets/{c.d.key}/concept")
    assert "aria-disabled='true' tabindex='0'" in response.text
    assert "aria-describedby='concept-discard-panel-unavailable'" in response.text
    assert DISCARD_UNAVAILABLE in response.text
    response = request(browser, "get", f"/datasets/{c.d.key}/concept/discard")
    assert response.status_code == 409 and DISCARD_UNAVAILABLE in response.text
    assert "id='discard-concept-form'" not in response.text
    response = request(
        browser,
        "post",
        f"/datasets/{c.d.key}/concept/discard",
        data={
            "csrf_token": csrf,
            "confirmation": "forged",
            "confirmed": "yes",
        },
    )
    assert response.status_code == 404
    assert snapshot(c) == before
    assert active(c) is not None


@pytest.mark.parametrize(
    "change", ["unchanged", "metadata", "fields", "identifier", "records", "mixed"]
)
def test_discard_removes_working_state_preserves_snapshots_and_governance(context, change):
    c = context
    prepared(c, extra=change in {"records", "mixed"})
    if change in {"metadata", "mixed"}:
        c.s.datasets.update_dataset(c.a.id, c.d.key, "Unpublished", "Private description")
    if change in {"fields", "mixed"}:
        c.s.dataset_fields.create_field(c.a.id, c.d.key, "extra", "Extra", "string")
        c.s.dataset_fields.update_field(
            c.a.id, c.d.key, "label", "New label", "string", False, False
        )
        fields = c.s.dataset_fields.list_fields(c.a.id, c.d.key)
        c.s.dataset_fields.replace_order(
            c.a.id, c.d.key, tuple(f.id for f in fields), tuple(f.id for f in reversed(fields))
        )
        if change == "fields":
            # Field deletion requires its working values to be cleared first.
            c.s.dataset_records.update_record(c.a.id, c.d.key, c.record.id, 1, {"code": "a"})
            c.s.dataset_fields.delete_field(c.a.id, c.d.key, "label")
    if change == "identifier":
        c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, 1)
        c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Label", "string", True, False)
        c.s.dataset_business_keys.set_business_key(c.a.id, c.d.key, ("code", "label"))
    if change in {"records", "mixed"}:
        c.s.dataset_records.update_record(
            c.a.id, c.d.key, c.retained.id, 1, {"code": "c", "label": "Changed"}
        )
        c.s.dataset_records.update_record(
            c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Secret"}
        )
        c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "b", "label": "Secret added"})
        c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, 2)
    c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", c.u.id)
    before = snapshot(c)
    review = c.service.review_discard(c.a.id, c.d.id)
    if change == "fields":
        assert (review.counts.fields_added, review.counts.fields_removed) == (1, 1)
    if change in {"records", "mixed"}:
        assert (
            review.counts.records_added,
            review.counts.records_changed,
            review.counts.records_removed,
        ) == (1, 1, 1)
    assert snapshot(c) == before
    result = discard(c, identity=review.identity)
    after = snapshot(c)
    deleted = {
        "dataset_concepts",
        "dataset_fields",
        "dataset_field_source_formats",
        "dataset_business_key_fields",
        "dataset_records",
        "dataset_record_values",
    }
    for table in before:
        if table in deleted:
            assert after[table] == []
        elif table not in {"datasets", "audit_events"}:
            assert after[table] == before[table], table
    assert active(c) is None
    current = c.service.read(c.a.id, c.d.key)
    assert current.context.dataset.name == (
        "Unpublished" if change in {"metadata", "mixed"} else "Business name"
    )
    assert current.context.dataset.description == (
        "Private description" if change in {"metadata", "mixed"} else "Secret description"
    )
    assert current.page.records[0].id == c.record.id
    events = [a for a in audits(c) if a.action == "dataset.concept_discarded"]
    assert len(events) == 1
    assert events[0].actor_user_id == c.a.id and events[0].object_id == c.d.id
    assert events[0].details == {
        "concept_id": review.identity.concept_id,
        "metadata_changes": result.metadata_changes,
        **{name: getattr(result.counts, name) for name in result.counts.__dataclass_fields__},
    }
    assert "Secret" not in json.dumps(events[0].details)
    stable = snapshot(c)
    with pytest.raises(ConflictError):
        discard(c, identity=review.identity)
    assert snapshot(c) == stable
    new = c.service.create_concept(c.a.id, c.d.id)
    assert new.id != review.identity.concept_id
    assert not c.service.concept_context(c.a.id, c.d.key).has_changes
    stable = snapshot(c)
    with pytest.raises(ConflictError, match="changed"):
        discard(c, identity=review.identity)
    assert snapshot(c) == stable


@pytest.mark.parametrize("kind", ["owner", "admin", "viewer", "editor", "export", "outsider"])
@pytest.mark.slow
def test_discard_authority(context, kind):
    c = context
    prepared(c)
    actor = c.a if kind == "admin" else c.u
    if kind == "owner":
        c.s.memberships.create_membership(c.a.id, actor.id, c.owner.id)
    elif kind in {"viewer", "editor"}:
        c.s.grants.create_grant(c.a.id, "user", actor.id, "dataset", c.d.id, kind)
    elif kind == "export":
        c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", actor.id)
    browser, csrf = client(c, actor)
    if kind in {"owner", "admin"}:
        payload = form(browser, c, csrf)
        assert (
            request(
                browser, "post", f"/datasets/{c.d.key}/concept/discard", data=payload
            ).status_code
            == 303
        )
    else:
        before = snapshot(c)
        with pytest.raises(NotFoundError):
            discard(c, actor.id)
        for method in ("get", "post"):
            response = request(
                browser, method, f"/datasets/{c.d.key}/concept/discard", data={"csrf_token": csrf}
            )
            assert response.status_code == 404
        assert snapshot(c) == before


@pytest.mark.parametrize(
    "state", ["revision", "concept_revision", "actor", "owner", "current", "mutation"]
)
def test_stale_identity_never_deletes(context, state):
    c = context
    prepared(c)
    identity = c.service.review_discard(c.a.id, c.d.id).identity
    if state == "mutation":
        c.s.dataset_fields.update_field(
            c.a.id, c.d.key, "label", "New changes", "string", False, False
        )
    else:
        values = {
            "revision": {"revision": identity.revision + 1},
            "concept_revision": {"start_revision": identity.start_revision + 1},
            "actor": {"actor_id": c.u.id},
            "owner": {"owner_team_id": c.other.id},
            "current": {"current_id": c.d.id},
        }
        identity = replace(identity, **values[state])
    before = snapshot(c)
    with pytest.raises(ConflictError, match="changed"):
        discard(c, identity=identity)
    assert snapshot(c) == before


@pytest.mark.parametrize("failure", ["values", "records", "fields", "concept", "audit", "commit"])
def test_discard_rollback(database_context, monkeypatch, failure):
    c = database_context
    prepared(c)
    identity = c.service.review_discard(c.a.id, c.d.id).identity
    before = snapshot(c)
    target = {
        "values": "dataset_record_values",
        "records": "dataset_records",
        "fields": "dataset_fields",
        "concept": "dataset_concepts",
    }.get(failure)

    def fail(*args, **kwargs):
        raise RuntimeError("injected failure")

    def after_delete(conn, cursor, statement, parameters, ctx, many):
        if target and statement.startswith(f"DELETE FROM {target} "):
            fail()

    event.listen(c.engine, "after_cursor_execute", after_delete)
    try:
        with monkeypatch.context() as patch:
            if failure == "audit":
                original = SqlAlchemyUnitOfWork.add_audit

                def fail_audit(self, **values):
                    original(self, **values)
                    self.flush()
                    fail()

                patch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_audit)
            elif failure == "commit":
                patch.setattr(Session, "commit", fail)
            with pytest.raises(RuntimeError, match="injected"):
                discard(c, identity=identity)
    finally:
        event.remove(c.engine, "after_cursor_execute", after_delete)
    assert snapshot(c) == before
    discard(c, identity=identity)
    assert active(c) is None


def test_concurrent_discard_at_most_once(database_context):
    c = database_context
    prepared(c)
    identity = c.service.review_discard(c.a.id, c.d.id).identity
    barrier = Barrier(2)

    def run():
        barrier.wait(timeout=10)
        try:
            discard(c, identity=identity)
            return True
        except ConflictError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(results) == [False, True]
    assert len([a for a in audits(c) if a.action == "dataset.concept_discarded"]) == 1


def test_import_tokens_cannot_attach_to_replacement_concept(context):
    c = context
    prepared(c)
    imports = c.s.record_imports
    staged, _, _ = imports.begin(c.a.id, "session", c.d.key, "rows.csv", b"code,label\nb,Secret\n")
    imports.preview(c.a.id, "session", c.d.key, staged.token, ("code", "label"))
    discard(c)
    c.service.create_concept(c.a.id, c.d.id)
    before = snapshot(c)
    with pytest.raises(NotFoundError):
        imports.confirm(c.a.id, "session", c.d.key, staged.token)
    assert imports.active(c.a.id, "session", c.d.key) is None
    assert staged.token not in imports.store._states
    assert snapshot(c) == before


def test_dialog_stale_refresh_redirect_and_form_security(context):
    c = context
    prepared(c)
    browser, csrf = client(c)
    path = f"/datasets/{c.d.key}/concept/discard"
    payload = form(browser, c, csrf)
    page = request(browser, "get", path)
    assert "data-initial-focus" in page.text and "data-panel-cancel" in page.text
    assert "Current, Scheduled and historical versions remain unchanged" in page.text
    assert "Café" not in page.text and "Secret description" not in page.text
    assert page.headers["Cache-Control"] == "no-store"
    records = request(browser, "get", f"/datasets/{c.d.key}/concept/records")
    elements = html_elements(records.text)
    assert sum(attrs.get("id") == "concept-discard-panel" for _, attrs in elements) == 1
    assert (
        sum(
            tag == "summary"
            and attrs.get("class") == "button button-secondary"
            and attrs.get("aria-label") == "Draft actions"
            for tag, attrs in elements
        )
        == 1
    )
    assert "aria-describedby='concept-discard-panel-description'" in records.text
    before = snapshot(c)
    for invalid in (
        {**payload, "csrf_token": "bad"},
        {**payload, "extra": "bad"},
        {k: v for k, v in payload.items() if k != "confirmed"},
        MultiDict([*payload.items(), ("confirmation", payload["confirmation"])]),
    ):
        assert request(browser, "post", path, data=invalid).status_code in {400, 403}
        assert snapshot(c) == before
    other, other_csrf = client(c)
    before = snapshot(c)
    assert (
        request(other, "post", path, data={**payload, "csrf_token": other_csrf}).status_code == 404
    )
    assert snapshot(c) == before
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "New changes", "string", False, False)
    before = snapshot(c)
    stale = request(browser, "post", path, data=payload)
    assert stale.status_code == 409 and "confirm discard again" in stale.text
    assert snapshot(c) == before
    fresh = form(browser, c, csrf)
    response = request(browser, "post", path, data=fresh)
    assert (
        response.status_code == 303 and response.location == f"/datasets/{c.d.key}?notice=discarded"
    )
    landed = request(browser, "get", response.location)
    assert "Draft discarded. Current was not changed." in landed.text
    assert ">Create draft</button>" in landed.text
    assert "New changes" not in landed.text
    before = snapshot(c)
    assert request(browser, "post", path, data=fresh).status_code == 409
    assert snapshot(c) == before


@pytest.mark.parametrize("cancelled", [False, True])
def test_scheduled_and_cancelled_baseline_do_not_block_discard_with_current(
    database_context, cancelled
):
    c = database_context
    current = prepared(c)
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Scheduled name", "Scheduled description")
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Scheduled name", "string", False, False
    )
    scheduled = publish(c, day=NOW.date() + timedelta(days=1))
    previous = c.service.create_concept(c.a.id, c.d.id)
    if cancelled:
        c.service.cancel(c.a.id, c.d.id, scheduled.id)
    before = snapshot(c)
    discard(c)
    after = snapshot(c)
    for name in before:
        if name.startswith("dataset_version"):
            assert before[name] == after[name]
    assert c.service.read(c.a.id, c.d.key).context.dataset.name == "Scheduled name"

    assert active(c) is None
    created = c.service.create_concept(c.a.id, c.d.id)
    expected_base = current if cancelled else scheduled
    assert created.id != previous.id
    assert created.base_version_id == expected_base.id
    concept = c.service.concept_context(c.a.id, c.d.key)
    assert not concept.has_changes
    assert c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key).name == "Scheduled name"
    recreated = snapshot(c)
    for name in before:
        if name.startswith("dataset_version"):
            assert recreated[name] == before[name]


def test_current_activation_requires_new_confirmation(context):
    c = context
    prepared(c)
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Scheduled", "")
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Scheduled", "string", False, False)
    publish(c, day=NOW.date() + timedelta(days=1))
    c.service.create_concept(c.a.id, c.d.id)
    identity = c.service.review_discard(c.a.id, c.d.id).identity
    c.service.clock.set(NOW + timedelta(days=1))
    before = snapshot(c)
    with pytest.raises(ConflictError, match="changed"):
        discard(c, identity=identity)
    assert snapshot(c) == before
    discard(c)
    assert c.service.read(c.a.id, c.d.key).context.dataset.name == "Scheduled"


@pytest.mark.parametrize("operation", ["record", "publish", "import"])
def test_concurrent_operation_and_discard_are_atomic(database_context, operation):
    c = database_context
    prepared(c)
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Publication candidate", "")
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Publication candidate", "string", False, False
    )
    imports = c.s.record_imports
    staged, _, _ = imports.begin(c.a.id, "session", c.d.key, "rows.csv", b"code,label\nb,Import\n")
    imports.preview(c.a.id, "session", c.d.key, staged.token, ("code", "label"))
    identity = c.service.review_discard(c.a.id, c.d.id).identity
    barrier = Barrier(2)

    def run(which):
        barrier.wait(timeout=10)
        try:
            if which == "discard":
                discard(c, identity=identity)
            elif operation == "record":
                c.s.dataset_records.update_record(
                    c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Changed"}
                )
            elif operation == "publish":
                publish(c, expected=identity.revision)
            else:
                imports.confirm(c.a.id, "session", c.d.key, staged.token)
            return which
        except (ConflictError, NotFoundError):
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("discard", "other")))
    assert sum(value is not None for value in results) == 1
    events = [a for a in audits(c) if a.action == "dataset.concept_discarded"]
    if "discard" in results:
        assert len(events) == 1 and active(c) is None
        after = snapshot(c)
        assert after["dataset_records"] == after["dataset_record_values"] == []
    else:
        assert not events
        assert (active(c) is None) == (operation == "publish")


@pytest.mark.parametrize("removal", ["discard", "dataset_delete"])
def test_discard_during_export_preparation_returns_no_artifact(context, monkeypatch, removal):
    from refrncr import dataset_export

    c = context
    prepared(c)
    entered, release = Event(), Event()
    original = dataset_export.verify_artifact_readable
    streams = []

    # Pause AFTER the read UoW closes, just before the export-audit UoW starts.
    # SQLite's read snapshot otherwise legitimately prevents a concurrent writer commit.
    original_exit = SqlAlchemyUnitOfWork.__exit__

    def exit_snapshot(self, *args):
        result = original_exit(self, *args)
        if not self.write and streams and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return result

    def capture(artifact):
        original(artifact)
        streams.append(artifact.content)

    monkeypatch.setattr(dataset_export, "verify_artifact_readable", capture)
    monkeypatch.setattr(SqlAlchemyUnitOfWork, "__exit__", exit_snapshot)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            c.s.dataset_records.export_dataset, c.a.id, c.d.key, "json", prefer_concept=True
        )
        assert entered.wait(10)
        try:
            if removal == "discard":
                discard(c)
            else:
                target = c.s.datasets.get_dataset_for_deletion(c.a.id, c.d.id)
                c.s.datasets.delete_dataset(c.a.id, c.d.id, target.name, target.expected_updated_at)
        finally:
            release.set()
        with pytest.raises(NotFoundError):
            future.result(timeout=10)
    assert streams[0].closed
    assert not [a for a in audits(c) if a.action == "dataset.export_requested"]


def test_membership_or_ownership_change_invalidates_confirmation(context):
    c = context
    prepared(c)
    membership = c.s.memberships.create_membership(c.a.id, c.u.id, c.owner.id)
    identity = c.service.review_discard(c.u.id, c.d.id).identity
    c.s.memberships.delete_membership(c.a.id, c.owner.id, membership.id)
    before = snapshot(c)
    with pytest.raises(NotFoundError):
        discard(c, c.u.id, identity)
    assert snapshot(c) == before

    admin_identity = c.service.review_discard(c.a.id, c.d.id).identity
    c.s.datasets.change_owner(c.a.id, c.d.key, c.owner.id, c.other.id)
    before = snapshot(c)
    with pytest.raises(ConflictError):
        discard(c, identity=admin_identity)
    assert snapshot(c) == before


def test_confirmed_import_proposals_removed_and_old_receipt_rejected(context):
    c = context
    prepared(c)
    imports = c.s.record_imports
    staged, _, _ = imports.begin(
        c.a.id, "session", c.d.key, "rows.csv", b"code,label\nb,Imported\n"
    )
    imports.preview(c.a.id, "session", c.d.key, staged.token, ("code", "label"))
    assert imports.confirm(c.a.id, "session", c.d.key, staged.token) == 1
    assert snapshot(c)["dataset_field_source_formats"]
    discard(c)
    assert snapshot(c)["dataset_field_source_formats"] == []
    c.service.create_concept(c.a.id, c.d.id)
    before = snapshot(c)
    with pytest.raises(NotFoundError):
        imports.state(c.a.id, "session", c.d.key, staged.token)
    assert snapshot(c) == before


@pytest.mark.parametrize("size", [0, 1000])
@pytest.mark.slow
def test_empty_and_large_mixed_discard_is_set_based(context, size, record_property):
    from time import perf_counter

    c = context
    setup(c, populated=False)
    if size:
        imports = c.s.record_imports
        rows = "code,label\n" + "".join(f"{n},Private source {n}\n" for n in range(size))
        staged, _, _ = imports.begin(c.a.id, "large", c.d.key, "rows.csv", rows.encode())
        imports.preview(c.a.id, "large", c.d.key, staged.token, ("code", "label"))
        assert imports.confirm(c.a.id, "large", c.d.key, staged.token) == size
    current = publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    if size:
        with c.s.datasets.uow() as uow:
            records = uow.versions.content(current).records
        c.s.dataset_records.update_record(
            c.a.id, c.d.key, records[0].id, 1, {"code": "changed", "label": "Private changed"}
        )
        c.s.dataset_records.delete_record(c.a.id, c.d.key, records[1].id, 1)
        c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "added"})
        c.s.dataset_fields.create_field(c.a.id, c.d.key, "extra", "Extra", "string")
        c.s.datasets.update_dataset(c.a.id, c.d.key, "Changed", "Changed description")
    before = snapshot(c)
    statements = []

    def capture(conn, cursor, statement, parameters, ctx, many):
        statements.append(statement)

    event.listen(c.engine, "before_cursor_execute", capture)
    start = perf_counter()
    try:
        review = c.service.review_discard(c.a.id, c.d.id)
        review_queries = len(statements)
        statements.clear()
        discard(c, identity=review.identity)
    finally:
        elapsed = perf_counter() - start
        event.remove(c.engine, "before_cursor_execute", capture)
    deletes = [s for s in statements if s.startswith("DELETE FROM")]
    assert len(deletes) == 6
    assert all("WHERE" in sql for sql in deletes)
    if size:
        assert (
            review.counts.records_added,
            review.counts.records_changed,
            review.counts.records_removed,
            review.counts.fields_added,
        ) == (1, 1, 1, 1)
        assert review.metadata_changes == 0
    else:
        assert review.counts.records_added == review.counts.records_removed == 0
    after = snapshot(c)
    assert after["dataset_records"] == after["dataset_fields"] == []
    for name in before:
        if name.startswith("dataset_version"):
            assert after[name] == before[name]
    record_property(
        "discard_measurement",
        json.dumps(
            {
                "engine": c.engine.dialect.name,
                "records": size,
                "review_queries": review_queries,
                "confirm_queries": len(statements),
                "deletes": len(deletes),
                "seconds": elapsed,
            }
        ),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "field_add",
        "field_edit",
        "field_delete",
        "field_order",
        "identifier",
        "record_add",
        "record_edit",
        "record_delete",
        "import",
        "publish",
    ],
)
def test_each_mutation_invalidates_open_discard(context, mutation):
    c = context
    prepared(c)
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "unused", "Unused", "string")
    if mutation == "identifier":
        c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, 1)
        c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Label", "string", True, False)
    identity = c.service.review_discard(c.a.id, c.d.id).identity
    fields = c.s.dataset_fields
    records = c.s.dataset_records
    if mutation == "field_add":
        fields.create_field(c.a.id, c.d.key, "extra", "Extra", "string")
    elif mutation == "field_edit":
        fields.update_field(c.a.id, c.d.key, "label", "Renamed", "string", False, False)
    elif mutation == "field_delete":
        fields.delete_field(c.a.id, c.d.key, "unused")
    elif mutation == "field_order":
        ids = tuple(f.id for f in fields.list_fields(c.a.id, c.d.key))
        fields.replace_order(c.a.id, c.d.key, ids, tuple(reversed(ids)))
    elif mutation == "identifier":
        c.s.dataset_business_keys.set_business_key(c.a.id, c.d.key, ("code", "label"))
    elif mutation == "record_add":
        records.create_record(c.a.id, c.d.key, {"code": "b", "unused": "x"})
    elif mutation == "record_edit":
        records.update_record(c.a.id, c.d.key, c.record.id, 1, {"code": "a", "unused": "x"})
    elif mutation == "record_delete":
        records.delete_record(c.a.id, c.d.key, c.record.id, 1)
    elif mutation == "import":
        imports = c.s.record_imports
        state, _, _ = imports.begin(c.a.id, "s", c.d.key, "rows.csv", b"code,unused\nb,x\n")
        imports.preview(c.a.id, "s", c.d.key, state.token, ("code", "unused"))
        imports.confirm(c.a.id, "s", c.d.key, state.token)
    else:
        # Publish a genuine Field change.
        fields.delete_field(c.a.id, c.d.key, "unused")
        fields.update_field(c.a.id, c.d.key, "label", "Published", "string", False, False)
        publish(c)
    before = snapshot(c)
    with pytest.raises(ConflictError):
        discard(c, identity=identity)
    assert snapshot(c) == before


def test_old_urls_and_posts_do_not_create_concept(context):
    c = context
    prepared(c)
    browser, csrf = client(c)
    discard(c)
    before = snapshot(c)
    for suffix in (
        "concept",
        "concept/records",
        "concept/discard",
        "fields/new",
        "records/new",
        "records/import",
        "concept/records/export?format=json",
    ):
        response = request(browser, "get", f"/datasets/{c.d.key}/{suffix}")
        assert response.status_code == 409, suffix
        assert response.headers["Cache-Control"] == "no-store"
    response = request(
        browser,
        "post",
        f"/datasets/{c.d.key}",
        data={
            "csrf_token": csrf,
            "name": "Old unpublished name",
            "description": "Old source",
        },
    )
    assert response.status_code == 302
    assert response.location.endswith("/configure")
    assert active(c) is None
    assert snapshot(c)["dataset_versions"] == before["dataset_versions"]
    assert request(browser, "get", f"/datasets/{c.d.key}/edit").status_code == 200


def test_discard_preserves_another_datasets_active_work(context):
    from sqlalchemy import select

    from refrncr.db.base import Base

    c = context
    prepared(c)
    other = c.s.datasets.create_dataset(c.a.id, "other-data", "Other work", "Keep", c.other.id)
    c.s.dataset_fields.create_field(c.a.id, other.key, "code", "Code", "string", True, True, True)
    c.s.dataset_records.create_record(c.a.id, other.key, {"code": "Other private value"})
    with c.sessions() as session:
        before = {
            table.name: list(session.execute(select(table).where(table.c.dataset_id == other.id)))
            for table in Base.metadata.sorted_tables
            if "dataset_id" in table.c
        }
    discard(c)
    with c.sessions() as session:
        after = {
            table.name: list(session.execute(select(table).where(table.c.dataset_id == other.id)))
            for table in Base.metadata.sorted_tables
            if "dataset_id" in table.c
        }
    assert after == before
    assert c.s.dataset_versions.concept_context(c.a.id, other.key).concept.dataset_id == other.id


def test_http_failure_rolls_back_and_logs_only_fixed_diagnostic(
    database_context, monkeypatch, caplog
):
    c = database_context
    prepared(c)
    browser, csrf = client(c)
    payload = form(browser, c, csrf)
    before = snapshot(c)
    original = SqlAlchemyUnitOfWork.add_audit

    def fail(self, **values):
        original(self, **values)
        self.flush()
        raise RuntimeError("Private source rows and filename must never be logged")

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
        response = request(browser, "post", f"/datasets/{c.d.key}/concept/discard", data=payload)
    assert response.status_code == 500
    assert "Error 500" in response.text
    assert "Dataset versions could not be loaded. Please try again." in response.text
    assert [record.getMessage() for record in caplog.records] == [
        "Dataset version operation failed: RuntimeError"
    ]
    assert all(record.exc_info is not None for record in caplog.records)
    assert snapshot(c) == before

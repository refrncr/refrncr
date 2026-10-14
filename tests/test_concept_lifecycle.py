"""Explicit lifecycle, atomicity and Current/Concept separation on both engines."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import select
from test_audit_coverage import audit_db, audits, context, snapshot  # noqa: F401
from test_dataset_version_views import client, request
from test_dataset_versions import NOW, publish, setup

from refrncr.dataset_diff import DatasetDiffService
from refrncr.models import DatasetConcept, DatasetVersion
from refrncr.services import ConflictError, NotFoundError


def active(c):
    with c.s.datasets.uow() as uow:
        return uow.concepts.get(c.d.id)


def test_first_concept_review_publication_and_closed_mutations(context):  # noqa: F811
    c = context
    setup(c)
    initial = active(c)
    assert initial.base_version_id is None and initial.creator_id == c.a.id
    diff = DatasetDiffService(c.service)
    summary = diff.open_review(c.a.id, c.d.id)
    assert summary.identity.baseline_id is None
    assert (summary.counts.fields_added, summary.counts.records_added) == (2, 1)
    first = publish(c)
    assert first.number == 1 and active(c) is None
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Live name", "")
    assert active(c) is None
    before = snapshot(c)
    with pytest.raises(ConflictError):
        c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "b"})
    assert snapshot(c) == before
    new = c.service.create_concept(c.a.id, c.d.id)
    assert new.id != initial.id and new.base_version_id == first.id
    assert not c.service.concept_context(c.a.id, c.d.key).has_changes
    before = snapshot(c)
    with pytest.raises(ConflictError, match="No changes"):
        diff.open_review(c.a.id, c.d.id)
    assert snapshot(c) == before


def test_duplicate_and_concurrent_creation_one_event(context):  # noqa: F811
    c = context
    setup(c)
    publish(c)
    before = len([a for a in audits(c) if a.action == "dataset.concept_created"])
    barrier = Barrier(2)

    def start():
        barrier.wait(timeout=10)
        try:
            return c.service.create_concept(c.a.id, c.d.id).id
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert sum(value is not None for value in results) == 1
    assert active(c).id in results
    events = [a for a in audits(c) if a.action == "dataset.concept_created"]
    assert len(events) == before + 1
    assert set(events[-1].details) == {"baseline_version_number", "concept_revision"}


def test_cancelled_basis_is_stale_without_losing_content(context):  # noqa: F811
    c = context
    setup(c)
    scheduled = publish(c, day=NOW.date() + timedelta(days=1))
    concept = c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Keep me"}
    )
    c.service.cancel(c.a.id, c.d.id, scheduled.id)
    state = c.service.concept_context(c.a.id, c.d.key)
    assert state.stale and not state.may_publish
    assert active(c).id == concept.id and active(c).base_version_id == scheduled.id
    before = snapshot(c)
    with pytest.raises(ConflictError):
        publish(c)
    with pytest.raises(ConflictError):
        c.service.create_concept(c.a.id, c.d.id)
    assert snapshot(c) == before
    # Further ordinary edits preserve the same concept and its cancelled basis.
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Preserved concept", "")
    assert active(c).base_version_id == scheduled.id
    view = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    assert {value.field_key: value.value for value in view.page.records[0].values}[
        "label"
    ] == "Keep me"
    with c.sessions() as session:
        assert len(session.scalars(select(DatasetVersion)).all()) == 1
        assert len(session.scalars(select(DatasetConcept)).all()) == 1
    with pytest.raises(NotFoundError):
        c.service.read(c.u.id, c.d.key)


def test_current_and_concept_navigation_preserve_dataset_status_value(context):  # noqa: F811
    c = context
    setup(c)
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "status", "Status", "string")
    c.s.dataset_records.update_record(
        c.a.id,
        c.d.key,
        c.record.id,
        1,
        {"code": "Betaling", "label": "Before", "status": "Complete"},
    )
    publish(c)
    browser, csrf = client(c)
    current = request(browser, "get", f"/datasets/{c.d.key}/records")
    assert current.status_code == 200 and "Before" in current.text
    assert "Add record" not in current.text and "Configure</a>" not in current.text
    assert "You’re working on a draft" not in current.text
    created = request(
        browser, "post", f"/datasets/{c.d.key}/concept/new", data={"csrf_token": csrf}
    )
    assert created.status_code == 303 and created.location.endswith("/concept/records")
    c.s.dataset_records.update_record(
        c.a.id,
        c.d.key,
        c.record.id,
        2,
        {"code": "Betaling", "label": "After", "status": "Complete"},
    )
    response = request(browser, "get", created.location)
    assert response.status_code == 200 and "Edited" in response.text and "Complete" in response.text
    assert "You’re working on a draft" in response.text
    state = snapshot(c)
    assert "Before" in request(browser, "get", f"/datasets/{c.d.key}/records").text
    assert snapshot(c) == state
    versions = request(browser, "get", f"/datasets/{c.d.key}/versions")
    assert "/concept/records" in versions.text and "View concept" not in versions.text
    outsider, _ = client(c, c.u)
    assert request(outsider, "get", created.location).status_code == 404


@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_create_failure_preserves_inactive_content_and_retry(context, monkeypatch, failure):  # noqa: F811
    from sqlalchemy.orm import Session

    from refrncr.persistence import SqlAlchemyUnitOfWork

    c = context
    setup(c)
    baseline = publish(c)
    before = snapshot(c)

    def fail(*args, **kwargs):
        raise RuntimeError("injected failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            SqlAlchemyUnitOfWork if failure == "audit" else Session,
            "add_audit" if failure == "audit" else "commit",
            fail,
        )
        with pytest.raises(RuntimeError, match="injected"):
            c.service.create_concept(c.a.id, c.d.id)
    assert snapshot(c) == before and active(c) is None
    assert c.service.create_concept(c.a.id, c.d.id).base_version_id == baseline.id


@pytest.mark.parametrize(
    "kind", ["owner", "admin", "viewer", "editor", "export", "outsider", "inactive"]
)
def test_concept_route_and_service_authority(context, kind):  # noqa: F811
    from uuid import uuid4

    c = context
    setup(c)
    publish(c)
    actor = c.a if kind == "admin" else c.u
    if kind in {"owner", "inactive"}:
        c.s.memberships.create_membership(c.a.id, actor.id, c.owner.id)
    if kind in {"viewer", "editor"}:
        c.s.grants.create_grant(c.a.id, "user", actor.id, "dataset", c.d.id, kind)
    if kind == "export":
        c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", actor.id)
    browser, csrf = client(c, actor)
    if kind == "inactive":
        c.s.users.disable_user(c.a.id, actor.id)
    url = f"/datasets/{c.d.key}/concept/new"
    before = snapshot(c)
    invalid_csrf = request(browser, "post", url, data={"csrf_token": "bad"})
    assert invalid_csrf.status_code in ({302} if kind == "inactive" else {403})
    if kind not in {"owner", "admin"}:
        with pytest.raises(NotFoundError):
            c.service.create_concept(actor.id, c.d.id)
        denied = request(browser, "post", url, data={"csrf_token": csrf})
        assert denied.status_code == (302 if kind == "inactive" else 404)
        assert snapshot(c) == before
        c.service.create_concept(c.a.id, c.d.id)
        c.s.datasets.update_dataset(c.a.id, c.d.key, "Private concept name", "")
        before = snapshot(c)
        for suffix in (
            "/concept",
            "/concept/records",
            "/concept/records/export?format=json",
            "/fields/new",
            "/records/new",
            "/records/import",
            "/versions/review",
            f"/versions/review/records/{c.record.id}?review=bad",
            "/versions/publish",
        ):
            response = request(browser, "get", f"/datasets/{c.d.key}{suffix}")
            assert response.status_code == (302 if kind == "inactive" else 404)
            assert "Private concept name" not in response.text
        assert snapshot(c) == before
    else:
        created = request(browser, "post", url, data={"csrf_token": csrf})
        assert created.status_code == 303
        assert active(c).creator_id == actor.id
        before = snapshot(c)
        assert request(browser, "post", url, data={"csrf_token": csrf}).status_code == 409
        assert snapshot(c) == before
    with pytest.raises(NotFoundError):
        c.service.create_concept(actor.id, str(uuid4()))


def test_closed_concept_guards_all_content_writers_and_old_urls(context):  # noqa: F811
    c = context
    setup(c)
    fields = c.s.dataset_fields.list_fields(c.a.id, c.d.key)
    publish(c)
    before = snapshot(c)
    operations = (
        lambda: c.s.dataset_fields.create_field(c.a.id, c.d.key, "extra", "Extra", "string"),
        lambda: c.s.dataset_fields.update_field(
            c.a.id, c.d.key, "label", "Label", "string", False, False
        ),
        lambda: c.s.dataset_fields.delete_field(c.a.id, c.d.key, "label"),
        lambda: c.s.dataset_fields.move_field(
            c.a.id, c.d.key, fields[0].id, "down", tuple(f.id for f in fields)
        ),
        lambda: c.s.dataset_business_keys.set_business_key(c.a.id, c.d.key, ("code",)),
        lambda: c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "b"}),
        lambda: c.s.dataset_records.update_record(c.a.id, c.d.key, c.record.id, 1, {"code": "a"}),
        lambda: c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, 1),
        lambda: c.s.record_imports.begin(c.a.id, "session", c.d.key, "rows.csv", b"code\nb\n"),
    )
    for operation in operations:
        with pytest.raises(ConflictError, match="New concept"):
            operation()
        assert snapshot(c) == before
    browser, csrf = client(c)
    before = snapshot(c)
    for suffix in (
        "/fields/new",
        "/fields/label/edit",
        "/records/new",
        "/records/import",
        "/versions/publish",
    ):
        response = request(browser, "get", f"/datasets/{c.d.key}{suffix}")
        assert response.status_code == 409
        assert "/concept/new" in response.text and "View current version" in response.text
    result = request(
        browser, "post", f"/datasets/{c.d.key}/records", data={"csrf_token": csrf, "code": "b"}
    )
    assert result.status_code == 409 and snapshot(c) == before


def test_new_concept_restores_latest_snapshot_after_cancel_without_active_concept(context):  # noqa: F811
    c = context
    setup(c)
    first = publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Scheduled content"}
    )
    scheduled = publish(c, day=NOW.date() + timedelta(days=1))
    assert active(c) is None
    c.service.cancel(c.a.id, c.d.id, scheduled.id)
    new = c.service.create_concept(c.a.id, c.d.id)
    assert new.base_version_id == first.id
    row = c.service.read(c.a.id, c.d.key, prefer_concept=True).page.records[0]
    assert row.id == c.record.id and row.revision > 2
    assert {v.field_key: v.value for v in row.values}["label"] == "Café"
    assert not c.service.concept_context(c.a.id, c.d.key).has_changes


def test_first_review_children_and_snapshot_export_selection(context):  # noqa: F811
    import json

    c = context
    setup(c)
    diff = DatasetDiffService(c.service)
    review = diff.open_review(c.a.id, c.d.id)
    before = snapshot(c)
    fields = diff.review(c.a.id, review.identity, "Fields")
    records = diff.review(c.a.id, review.identity, "Records")
    assert {field.status for field in fields.items} == {"Added"}
    assert records.items[0].status == "Added"
    detail = diff.record_detail(c.a.id, review.identity, c.record.id)
    assert all(value.before is None for value in detail.values)
    assert {value.after for value in detail.values} == {"a", "Café"}
    assert snapshot(c) == before
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Draft value"}
    )
    for prefer_concept, expected in ((False, "Café"), (True, "Draft value")):
        artifact = c.s.dataset_records.export_dataset(
            c.a.id, c.d.key, "json", prefer_concept=prefer_concept
        )
        try:
            assert json.load(artifact.content) == [{"code": "a", "label": expected}]
        finally:
            artifact.content.close()


def test_paged_record_badges_use_stable_identity_and_shared_classification(context):  # noqa: F811
    c = context
    setup(c)
    rows = [
        c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": f"b{n:02}"}) for n in range(50)
    ]
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "zz", "label": "Café"}
    )
    added = c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "aaa"})
    c.s.dataset_records.delete_record(c.a.id, c.d.key, rows[5].id, 1)
    first = c.service.read(c.a.id, c.d.key, page=1, prefer_concept=True)
    second = c.service.read(c.a.id, c.d.key, page=2, prefer_concept=True)
    assert first.page.total == second.page.total == 52
    assert first.context.record_count == second.context.record_count == 51
    assert first.deleted_count == second.deleted_count == 1
    assert first.record_states[added.id] == "Added"
    assert first.record_states[rows[5].id] == "Deleted"
    assert second.record_states[c.record.id] == "Edited"
    ids = [r.id for page in (first, second) for r in page.page.records]
    assert len(ids) == len(set(ids)) == 52
    assert rows[5].id in ids
    found = c.service.read(c.a.id, c.d.key, prefer_concept=True, search="b05")
    assert [r.id for r in found.page.records] == [rows[5].id]
    assert found.record_states == {rows[5].id: "Deleted"}
    assert found.page.total == 1 and found.context.record_count == 51
    summary = c.service.concept_context(c.a.id, c.d.key).summary.counts
    assert (summary.records_added, summary.records_changed, summary.records_removed) == (1, 1, 1)
    review = DatasetDiffService(c.service).open_review(c.a.id, c.d.id)
    assert review.counts.records_removed == 1


def test_cancel_audit_failure_rolls_back_staleness_and_preserves_changes(context, monkeypatch):  # noqa: F811
    from refrncr.persistence import SqlAlchemyUnitOfWork

    c = context
    setup(c)
    scheduled = publish(c, day=NOW.date() + timedelta(days=1))
    c.service.create_concept(c.a.id, c.d.id)
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Preserve this concept", "")
    before = snapshot(c)
    original = SqlAlchemyUnitOfWork.add_audit

    def fail(self, **values):
        original(self, **values)
        raise RuntimeError("injected audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
        with pytest.raises(RuntimeError, match="injected"):
            c.service.cancel(c.a.id, c.d.id, scheduled.id)
    assert snapshot(c) == before
    assert not c.service.concept_context(c.a.id, c.d.key).stale
    c.service.cancel(c.a.id, c.d.id, scheduled.id)
    assert c.service.concept_context(c.a.id, c.d.key).stale

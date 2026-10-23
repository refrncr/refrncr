"""Explicit source upgrades, including persistence contracts on both engines."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from support import NOW, snapshot
from test_mapping_source_versions import create_mapping, prepare, publish_table

from refrncr.dataset_diff import DatasetDiffService
from refrncr.models import AuditEvent
from refrncr.services import ConflictError, NotFoundError, ValidationError


def rows(c, mapping):
    view = c.s.dataset_versions.read(c.a.id, mapping.key, prefer_concept=True)
    return {
        next(value.value for value in row.values if value.field_key == "code"): row
        for row in view.page.records
        if view.record_states.get(row.id) != "Deleted"
    }


def values(row):
    return {value.field_key: value.value for value in row.values}


def update(c, mapping, actor=None, revision=None):
    return c.s.dataset_versions.update_mapping_source(
        actor or c.a.id,
        mapping.id,
        revision
        if revision is not None
        else c.s.datasets.get_dataset_for_edit(c.a.id, mapping.key).revision,
    )


def fixture(c, *, required=False, publish=True):
    prepare(c)
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "b", "label": "Old B"})
    first = publish_table(c, c.d)
    mapping = create_mapping(c)
    c.s.dataset_fields.create_field(
        c.a.id,
        mapping.key,
        "label",
        "Label",
        "string",
        mapping_role="source_context",
        source_field_key="label",
    )
    c.s.dataset_fields.create_field(
        c.a.id,
        mapping.key,
        "target",
        "Target",
        "string",
        is_required=False,
    )
    for code, row in rows(c, mapping).items():
        c.s.dataset_records.update_record(
            c.a.id, mapping.key, row.id, row.revision, {"target": code.upper()}
        )
    if required:
        c.s.dataset_fields.update_field(
            c.a.id, mapping.key, "target", "Target", "string", True, False
        )
    published = publish_table(c, mapping) if publish else None
    return mapping, first, published


def next_source(c, *, day=1):
    c.s.dataset_versions.create_concept(c.a.id, c.d.id)
    for row in c.s.dataset_records.list_records(c.a.id, c.d.key).records:
        if values(row)["code"] == "b":
            c.s.dataset_records.delete_record(c.a.id, c.d.key, row.id, row.revision)
        else:
            c.s.dataset_records.update_record(
                c.a.id, c.d.key, row.id, row.revision, {"code": "a", "label": "Updated A"}
            )
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "c", "label": "New C"})
    return publish_table(c, c.d, NOW.date() + timedelta(days=day))


@pytest.mark.parametrize("existing_draft", [False, True])
def test_upgrade_reconciles_and_preserves_targets(database_context, existing_draft):
    c = database_context
    mapping, first, published = fixture(c)
    if existing_draft:
        concept = c.s.dataset_versions.create_concept(c.a.id, mapping.id)
        row = rows(c, mapping)["a"]
        c.s.dataset_records.update_record(
            c.a.id, mapping.key, row.id, row.revision, {"target": "Draft edit"}
        )
    second = next_source(c)
    result = update(c, mapping)
    if existing_draft:
        assert result.id == concept.id
    assert c.s.datasets.get_dataset_for_edit(c.a.id, mapping.key).source_version_id == second.id
    assert values(rows(c, mapping)["a"]) == {
        "code": "a",
        "label": "Updated A",
        "target": "Draft edit" if existing_draft else "A",
    }
    assert values(rows(c, mapping)["c"]) == {"code": "c", "label": "New C"}
    view = c.s.dataset_versions.read(c.a.id, mapping.key, prefer_concept=True)
    assert view.page.total == 3
    assert set(view.mapping_record_states.values()) == {"Unmapped", "Removed from source"}
    assert c.s.datasets.mapping_source(c.a.id, mapping.key)["draft"] == {
        "unmapped": 1,
        "removed": 1,
        "required_target": False,
        "previous_number": 1,
    }
    for status, code in (("unmapped", "c"), ("removed", "b")):
        filtered = c.s.dataset_versions.read(
            c.a.id, mapping.key, prefer_concept=True, mapping_status=status
        )
        assert filtered.page.total == 1
        assert values(filtered.page.records[0])["code"] == code
    diff = DatasetDiffService(c.s.dataset_versions).open_review(c.a.id, mapping.id)
    assert diff.metadata[0].before == "Business name V1"
    assert diff.metadata[0].after == "Business name V2"
    assert (diff.counts.records_added, diff.counts.records_removed) == (1, 1)
    with pytest.raises(ValidationError, match="precede"):
        publish_table(c, mapping)
    version = publish_table(c, mapping, second.effective_date)
    assert version.source_version_id == second.id
    assert version.record_count == 2
    current = c.s.dataset_versions.read(c.a.id, mapping.key, version.id)
    assert {values(row)["code"] for row in current.page.records} == {"a", "c"}
    old = c.s.dataset_versions.read(c.a.id, mapping.key, published.id)
    assert {values(row)["code"] for row in old.page.records} == {"a", "b"}
    assert c.s.datasets.mapping_source(c.a.id, mapping.key, published.id)["version_id"] == first.id
    with c.sessions() as session:
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "dataset.mapping_source_updated")
        )
        assert event.actor_user_id == c.a.id
        assert event.details == {"previous_version": 1, "source_version": 2}


def test_required_target_blocks_publication_until_mapped(database_context):
    c = database_context
    mapping, _, _ = fixture(c, required=True)
    second = next_source(c)
    update(c, mapping)
    before = snapshot(c)
    with pytest.raises(ValidationError, match="invalid values") as failure:
        publish_table(c, mapping, second.effective_date)
    assert failure.value.errors == {"target": "A value is required"}
    assert snapshot(c) == before
    row = rows(c, mapping)["c"]
    c.s.dataset_records.update_record(c.a.id, mapping.key, row.id, row.revision, {"target": "C"})
    assert c.s.datasets.mapping_source(c.a.id, mapping.key)["draft"]["unmapped"] == 0
    assert publish_table(c, mapping, second.effective_date).record_count == 2


@pytest.mark.parametrize("intermediate_update", [False, True])
def test_update_uses_latest_source_across_multiple_versions(database_context, intermediate_update):
    c = database_context
    mapping, _, _ = fixture(c)
    next_source(c)
    if intermediate_update:
        update(c, mapping)
    c.service.clock.set(NOW + timedelta(days=1))
    c.s.dataset_versions.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "b", "label": "Returned B"})
    third = publish_table(c, c.d, NOW.date() + timedelta(days=2))
    update(c, mapping)
    assert c.s.datasets.mapping_source(c.a.id, mapping.key)["number"] == 3
    assert values(rows(c, mapping)["b"]) == {"code": "b", "label": "Returned B", "target": "B"}
    assert c.s.datasets.mapping_source(c.a.id, mapping.key)["draft"]["removed"] == 0
    assert publish_table(c, mapping, third.effective_date).source_version_id == third.id


def test_source_only_update_is_reviewable_and_publishable(database_context):
    c = database_context
    mapping, _, _ = fixture(c)
    c.s.dataset_versions.create_concept(c.a.id, c.d.id)
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "unused", "Unused", "string")
    second = publish_table(c, c.d, NOW.date() + timedelta(days=1))
    update(c, mapping)
    summary = DatasetDiffService(c.s.dataset_versions).open_review(c.a.id, mapping.id)
    assert summary.has_changes
    assert (
        summary.counts.records_added
        == summary.counts.records_changed
        == summary.counts.records_removed
        == 0
    )
    assert c.s.dataset_versions.history(c.a.id, mapping.key).concept_summary.can_review
    assert publish_table(c, mapping, second.effective_date).source_version_id == second.id


def test_source_update_guards_and_noop_preserve_state(database_context):
    c = database_context
    mapping, _, _ = fixture(c)
    next_source(c)
    before = snapshot(c)
    with pytest.raises(NotFoundError):
        update(c, mapping, actor=c.u.id)
    with pytest.raises(ConflictError):
        update(c, mapping, revision=0)
    assert snapshot(c) == before
    update(c, mapping)
    after = snapshot(c)
    update(c, mapping)
    assert snapshot(c) == after


def test_failed_source_update_rolls_back_draft_and_audit(database_context, monkeypatch):
    c = database_context
    mapping, _, _ = fixture(c)
    next_source(c)
    c.s.dataset_versions.create_concept(c.a.id, mapping.id)
    before = snapshot(c)

    def fail(*args, **kwargs):
        raise RuntimeError("Injected audit failure")

    monkeypatch.setattr("refrncr.dataset_versions.record_audit", fail)
    with pytest.raises(RuntimeError):
        update(c, mapping)
    assert snapshot(c) == before


def test_first_draft_removed_rows_are_visible_and_discard_restores_pin(database_context):
    c = database_context
    mapping, _, _ = fixture(c, publish=False)
    second = next_source(c)
    update(c, mapping)
    view = c.s.dataset_versions.read(
        c.a.id, mapping.key, prefer_concept=True, mapping_status="removed"
    )
    assert view.page.total == 1
    assert values(view.page.records[0])["target"] == "B"
    publish_table(c, mapping, second.effective_date)
    c.service.clock.set(NOW + timedelta(days=1))
    c.s.dataset_versions.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "d"})
    publish_table(c, c.d, NOW.date() + timedelta(days=2))
    update(c, mapping)
    review = c.s.dataset_versions.review_discard(c.a.id, mapping.id)
    c.s.dataset_versions.discard_concept(c.a.id, mapping.id, review.identity)
    c.s.dataset_versions.create_concept(c.a.id, mapping.id)
    assert c.s.datasets.get_dataset_for_edit(c.a.id, mapping.key).source_version_id == second.id
    assert set(rows(c, mapping)) == {"a", "c"}


def browser(c):
    from support import login

    from refrncr.web import create_flask_app

    client = create_flask_app(
        c.s.users,
        c.s.authentication,
        c.s.sessions,
        c.s.teams,
        c.s.memberships,
        authorization=c.s.authorization,
        datasets=c.s.datasets,
        dataset_fields=c.s.dataset_fields,
        dataset_business_keys=c.s.dataset_business_keys,
        dataset_records=c.s.dataset_records,
        dataset_versions=c.s.dataset_versions,
    ).test_client()
    login(client, password="valid password")
    return client


def test_browser_update_filters_review_and_publication(context):
    from support import body, html_elements, html_text, request, session_csrf

    c = context
    mapping, _, _ = fixture(c)
    client = browser(c)
    path = f"/datasets/{mapping.key}"
    assert "Source update" not in body(request(client, "get", "/datasets?kind=mapping"))
    second = next_source(c)
    page = body(request(client, "get", path + "/records"))
    assert "has a newer version (V2). This mapping uses V1." in page
    assert "Update to V2" in page
    assert "is current since" not in page
    assert "Source update" in body(request(client, "get", "/datasets?kind=mapping"))
    revision = c.s.datasets.get_dataset_for_edit(c.a.id, mapping.key).revision
    result = request(
        client,
        "post",
        path + "/source/update",
        data={
            "expected_revision": revision,
            "csrf_token": session_csrf(client, c.s),
        },
    )
    assert result.status_code == 303
    page = body(request(client, "get", result.location))
    assert "Removed from source" in page and "Unmapped" in page
    assert "Undo delete" not in page
    assert "Update to V2" not in page
    for selected, present, absent in (
        ("unmapped", "New C", "Updated A"),
        ("removed", "Old B", "New C"),
    ):
        filtered = request(client, "get", path + "/concept/records?mapping_status=" + selected)
        assert filtered.status_code == 200
        assert present in body(filtered) and absent not in body(filtered)
        assert "Showing 1–1 of 1" in html_text(body(filtered))
    reviewed = request(client, "get", path + "/versions/review", follow_redirects=True)
    assert reviewed.status_code == 200
    assert "Source: Business name V1 → V2" in html_text(body(reviewed))
    assert "1 unmapped · 1 removed from source" in html_text(body(reviewed))
    assert "1 unmapped row will be published" in html_text(body(reviewed))
    publication = request(client, "get", path + "/versions/publish")
    assert publication.status_code == 200
    inputs = {
        attrs.get("name"): attrs
        for tag, attrs in html_elements(body(publication))
        if tag == "input"
    }
    assert inputs["effective_date"]["value"] == second.effective_date.isoformat()
    assert inputs["effective_date"]["min"] == second.effective_date.isoformat()
    posted = request(
        client,
        "post",
        path + "/versions/publish",
        data={
            "csrf_token": session_csrf(client, c.s),
            "dataset_id": mapping.id,
            "expected_revision": inputs["expected_revision"]["value"],
            "effective_date": second.effective_date.isoformat(),
            "release_note": "",
            "confirmed": "yes",
        },
    )
    assert posted.status_code == 303


def test_browser_source_current_warning_and_post_guards(context):
    from support import body, login, request, session_csrf

    c = context
    mapping, _, _ = fixture(c)
    next_source(c)
    c.service.clock.set(NOW + timedelta(days=1))
    client = browser(c)
    path = f"/datasets/{mapping.key}"
    page = body(request(client, "get", path + "/records"))
    assert "alert-warning" in page and "V2 is current since" in page
    assert (
        request(client, "post", path + "/source/update", data={"expected_revision": 1}).status_code
        == 403
    )
    assert (
        request(
            client,
            "post",
            path + "/source/update",
            data={
                "csrf_token": session_csrf(client, c.s),
                "expected_revision": 1,
            },
        ).status_code
        == 409
    )
    for invalid in ("0", "bad", ""):
        assert (
            request(
                client,
                "post",
                path + "/source/update",
                data={
                    "csrf_token": session_csrf(client, c.s),
                    "expected_revision": invalid,
                },
            ).status_code
            == 400
        )
    assert request(client, "get", path + "/source/update").status_code == 405
    request(client, "post", "/logout", data={"csrf_token": session_csrf(client, c.s)})
    login(client, email="user@example.com", password="valid password")
    page = body(request(client, "get", path + "/records"))
    assert "Update to V2" not in page
    assert (
        request(
            client,
            "post",
            path + "/source/update",
            data={
                "csrf_token": session_csrf(client, c.s),
                "expected_revision": 1,
            },
        ).status_code
        == 404
    )
    assert (
        request(
            client,
            "post",
            "/datasets/missing/source/update",
            data={
                "csrf_token": session_csrf(client, c.s),
                "expected_revision": 1,
            },
        ).status_code
        == 404
    )


def test_removed_mapping_row_cannot_be_restored_directly(database_context):
    c = database_context
    mapping, _, _ = fixture(c)
    next_source(c)
    concept = update(c, mapping)
    removed = c.s.dataset_versions.read(
        c.a.id, mapping.key, prefer_concept=True, mapping_status="removed"
    ).page.records[0]
    before = snapshot(c)
    with pytest.raises(ValidationError, match="source version"):
        c.s.dataset_records.restore_record(
            c.a.id, mapping.key, removed.id, removed.revision, concept_id=concept.id
        )
    assert snapshot(c) == before


def test_incompatible_source_update_is_atomic(database_context):
    c = database_context
    mapping, first, _ = fixture(c)
    c.s.dataset_versions.create_concept(c.a.id, c.d.id)
    for row in c.s.dataset_records.list_records(c.a.id, c.d.key).records:
        c.s.dataset_records.update_record(
            c.a.id, c.d.key, row.id, row.revision, {"code": values(row)["code"]}
        )
    c.s.dataset_fields.delete_field(c.a.id, c.d.key, "label")
    publish_table(c, c.d, NOW.date() + timedelta(days=1))
    before = snapshot(c)
    with pytest.raises(ValidationError, match="retain the selected source fields"):
        update(c, mapping)
    assert snapshot(c) == before
    assert c.s.datasets.mapping_source(c.a.id, mapping.key)["version_id"] == first.id


def test_source_upgrade_invalidates_open_review(database_context):
    c = database_context
    mapping, _, _ = fixture(c)
    c.s.dataset_versions.create_concept(c.a.id, mapping.id)
    row = rows(c, mapping)["a"]
    c.s.dataset_records.update_record(
        c.a.id, mapping.key, row.id, row.revision, {"target": "Edited"}
    )
    diff = DatasetDiffService(c.s.dataset_versions)
    identity = diff.open_review(c.a.id, mapping.id).identity
    next_source(c)
    update(c, mapping)
    with pytest.raises(ConflictError, match="changed"):
        diff.review(c.a.id, identity)


def test_current_warning_survives_newer_scheduled_source(context):
    from support import body, request

    c = context
    mapping, _, _ = fixture(c)
    next_source(c)
    c.service.clock.set(NOW + timedelta(days=1))
    c.s.dataset_versions.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "d"})
    publish_table(c, c.d, NOW.date() + timedelta(days=2))
    page = body(request(browser(c), "get", f"/datasets/{mapping.key}/records"))
    assert "V2 is current since" in page
    assert "Update to V3" in page
    assert "alert-warning" in page


def test_update_after_cancelled_mapping_uses_restored_baseline(database_context):
    c = database_context
    mapping, _, _ = fixture(c)
    second = next_source(c)
    update(c, mapping)
    scheduled = publish_table(c, mapping, second.effective_date)
    c.s.dataset_versions.cancel(c.a.id, mapping.id, scheduled.id)
    update(c, mapping)
    assert c.s.datasets.get_dataset_for_edit(c.a.id, mapping.key).source_version_id == second.id
    assert set(rows(c, mapping)) == {"a", "c"}
    assert values(rows(c, mapping)["a"])["target"] == "A"
    assert [
        field.mapping_role for field in c.s.dataset_fields.list_fields(c.a.id, mapping.key)
    ] == [
        "source_identifier",
        "source_context",
        "mapping_target",
    ]
    assert publish_table(c, mapping, second.effective_date).record_count == 2

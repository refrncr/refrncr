"""Record metadata and retained baseline tombstones on both supported engines."""

import re

import pytest
from sqlalchemy import event
from test_audit_coverage import audit_db, context, snapshot  # noqa: F401
from test_dataset_version_views import client, exported_rows, request
from test_dataset_versions import publish, setup

from refrncr.services import NotFoundError


def test_system_status_is_distinct_from_content_and_tombstones_are_read_only(context):  # noqa: F811
    c = context
    setup(c)
    for key in ("status", "change", "validation"):
        c.s.dataset_fields.create_field(c.a.id, c.d.key, key, key.title(), "string")
    content = {
        "code": "a",
        "label": "Café",
        "status": "Complete",
        "change": "Content change",
        "validation": "Content validation",
    }
    c.s.dataset_records.update_record(c.a.id, c.d.key, c.record.id, 1, content)
    deleted = c.s.dataset_records.create_record(
        c.a.id, c.d.key, {**content, "code": "Old <identifier>"}
    )
    untouched = c.s.dataset_records.create_record(c.a.id, c.d.key, {**content, "code": "untouched"})
    first = publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 2, {**content, "label": "Edited value"}
    )
    c.s.dataset_records.delete_record(c.a.id, c.d.key, deleted.id, 1)
    added = c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "new"})
    browser, csrf = client(c)
    ordinary, _ = client(c, c.u)
    before = snapshot(c)
    view = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    assert view.record_states == {
        c.record.id: "Edited",
        deleted.id: "Deleted",
        untouched.id: "Published",
        added.id: "Added",
    }
    concept_html = request(
        browser, "get", f"/datasets/{c.d.key}/concept/records?record_status=all"
    ).text
    statuses = re.findall(
        r"<td class='record-system-status'><span class='badge ([^']+)'>([^<]+)</span></td>",
        concept_html,
    )
    assert sorted(statuses) == sorted(
        [
            ("badge-published", "Published"),
            ("badge-draft", "Edited"),
            ("badge-draft", "Added"),
            ("badge-removed", "Deleted"),
        ]
    )
    assert view.context.record_count == 3 and view.deleted_count == 1 and view.page.total == 4
    response = request(
        browser, "get", f"/datasets/{c.d.key}/concept/records?q=old&record_status=deleted"
    )
    assert response.status_code == 200
    html = response.text
    assert "3 records" in html and "1 marked for deletion" in html
    headers = re.findall(r"<th\b[^>]*>(.*?)</th>", html, re.S)
    assert [re.sub(r"<[^>]+>", "", h).strip() for h in headers[-4:-1]] == [
        "Status",
        "Change",
        "Validation",
    ]
    assert "Status (application metadata)" in html
    assert "Complete" in html and "badge-success" not in html
    assert "Old &lt;identifier&gt;" in html and "Old <identifier>" not in html
    panel = html.split(f"id='record-panel-{deleted.id}'", 1)[1].split("</dialog>", 1)[0]
    assert "This Record will be removed when you publish" in panel
    assert "data-panel-mode=" not in panel and "data-panel-view='edit'" not in panel
    assert "Content change" in panel and "Content validation" in panel
    assert snapshot(c) == before  # Read/search/details do not write or audit.
    for operation in (
        lambda: c.s.dataset_records.update_record(c.a.id, c.d.key, deleted.id, 1, content),
        lambda: c.s.dataset_records.delete_record(c.a.id, c.d.key, deleted.id, 1),
    ):
        with pytest.raises(NotFoundError):
            operation()
    for suffix in ("edit", "delete"):
        rejected = request(
            browser,
            "post",
            f"/datasets/{c.d.key}/records/{deleted.id}/{suffix}",
            data={"csrf_token": csrf, "expected_revision": "1", "confirm": "yes", **content},
        )
        assert rejected.status_code == 404
    assert request(ordinary, "get", f"/datasets/{c.d.key}/concept/records?q=old").status_code == 404
    assert snapshot(c) == before
    publish(c)
    current = c.service.read(c.a.id, c.d.key)
    assert current.deleted_count == 0 and current.page.total == 3
    assert deleted.id not in current.record_states
    assert set(current.record_states.values()) == {"Published"}
    for route in (
        f"/datasets/{c.d.key}/records",
        f"/datasets/{c.d.key}/versions/{first.id}/records",
    ):
        response = request(browser, "get", route)
        assert response.status_code == 200
        html = response.text
        assert "record-system-status" not in html
        assert "Status (application metadata)" not in html
        content_headers = re.findall(r"<th\b[^>]*>(.*?)</th>", html, re.S)
        assert [re.sub(r"<[^>]+>", "", h).strip() for h in content_headers[2:5]] == [
            "Status",
            "Change",
            "Validation",
        ]
        assert ">Complete</span>" in html
    rows = exported_rows(
        c.s.dataset_records.export_dataset(c.a.id, c.d.key, "csv", version_id=first.id)
    )
    assert len(rows) == 3
    assert all(set(row) == {"code", "label", "status", "change", "validation"} for row in rows)
    assert all(row["status"] == "Complete" for row in rows)


def test_tombstone_old_fields_and_identifier_survive_schema_changes(context):  # noqa: F811
    c = context
    setup(c, populated=False)
    old = c.s.dataset_records.create_record(
        c.a.id, c.d.key, {"code": "OLD", "label": "Retained old value"}
    )
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.delete_record(c.a.id, c.d.key, old.id, 1)
    c.s.dataset_fields.delete_field(c.a.id, c.d.key, "label")
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "label", "Replacement", "integer")
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "code", "Code", "integer", True, True)
    new = c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "12", "label": "7"})
    for _ in range(2):
        view = c.service.read(c.a.id, c.d.key, prefer_concept=True)
        assert [r.id for r in view.page.records] == [new.id, old.id]
    browser, _ = client(c)
    # The old identifier is now Integer in the displayed schema: REF-136 does
    # not search the retained String as if it were the current Integer Field.
    html = request(
        browser, "get", f"/datasets/{c.d.key}/concept/records?record_status=deleted"
    ).text
    panel = html.split(f"id='record-panel-{old.id}'", 1)[1].split("</dialog>", 1)[0]
    assert "Retained old value" in panel and "Replacement" not in panel
    assert "OLD" in panel


def test_tombstone_query_count_is_constant_and_search_paginates_combined_rows(context):  # noqa: F811
    c = context
    setup(c, populated=False)
    records = [
        c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": f"same-{n:03}"})
        for n in range(60)
    ]
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)

    def measured():
        statements = []
        engine = c.sessions.kw["bind"]

        def track(_connection, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", track)
        try:
            return c.service.read(c.a.id, c.d.key, prefer_concept=True, search="SAME"), len(
                statements
            )
        finally:
            event.remove(engine, "before_cursor_execute", track)

    c.s.dataset_records.delete_record(c.a.id, c.d.key, records[0].id, 1)
    _, small = measured()
    for record in records[1:]:
        c.s.dataset_records.delete_record(c.a.id, c.d.key, record.id, 1)
    first, large = measured()
    assert small == large
    second = c.service.read(c.a.id, c.d.key, page=2, prefer_concept=True, search="same")
    assert first.context.record_count == 0 and first.deleted_count == 60
    assert first.page.total == second.page.total == 60
    assert [r.id for r in (*first.page.records, *second.page.records)] == [r.id for r in records]
    assert set(first.record_states.values()) == {"Deleted"}
    browser, _ = client(c)
    html = request(
        browser, "get", f"/datasets/{c.d.key}/concept/records?q=same&record_status=all"
    ).text
    assert "page=2" in html and "q=same" in html
    empty = c.service.read(c.a.id, c.d.key, prefer_concept=True, search="no-match")
    assert empty.page.total == 0 and empty.context.record_count == 0 and empty.deleted_count == 60


def test_field_only_changes_keep_surviving_record_published(context):  # noqa: F811
    c = context
    setup(c)
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "new", "New", "string")
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Renamed", "string", False, False)
    view = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    assert view.record_states == {c.record.id: "Published"}
    assert c.service.concept_context(c.a.id, c.d.key).summary.counts.records_changed == 0


def test_recreated_identifier_has_distinct_added_and_deleted_rows(context):  # noqa: F811
    c = context
    setup(c)
    assert c.service.read(c.a.id, c.d.key, prefer_concept=True).record_states == {
        c.record.id: "Added"
    }
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, 1)
    replacement = c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "a"})
    view = c.service.read(c.a.id, c.d.key, prefer_concept=True, search="a")
    assert [r.id for r in view.page.records] == sorted([c.record.id, replacement.id])
    assert view.record_states == {c.record.id: "Deleted", replacement.id: "Added"}
    assert view.context.record_count == view.deleted_count == 1

"""REF-160 complete-file and retained Record lifecycle, on both engines."""

import pytest
from sqlalchemy import select
from support import content, dataset_version_client, publish, request, setup, snapshot

from refrncr.models import AuditEvent, DatasetConcept, DatasetRecord
from refrncr.record_import import ImportValidationError
from refrncr.services import ConflictError, NotFoundError


def clean(c):
    setup(c)
    first = publish(c)
    concept = c.service.create_concept(c.a.id, c.d.id)
    return first, concept


def preview(c, source=b"code,label\na,Cafe\nb,New\n", *, session="new"):
    state, _, _ = c.s.record_imports.begin(
        c.a.id, session, c.d.key, "records.csv", source, mode="new_version"
    )
    result = c.s.record_imports.preview(c.a.id, session, c.d.key, state.token, ("code", "label"))
    return c.s.record_imports.state(c.a.id, session, c.d.key, state.token), result


def confirm(c, state, **kwargs):
    return c.s.record_imports.confirm(
        c.a.id, state.session_id, c.d.key, state.token, confirmation=state.confirmation, **kwargs
    )


def test_complete_import_and_unchanged_revision(context):
    c = context
    first, _ = clean(c)
    before = content(c, first)
    state, result = preview(c, "code,label\na,Cafe\u0301\nb,New\n".encode())
    assert result.valid
    assert result.counts == {
        "Added": 1,
        "Changed": 0,
        "Unchanged": 1,
        "Deleted": 0,
        "Error": 0,
        "matching": 1,
    }
    assert len(result.differences) == 1
    assert confirm(c, state) == 2
    assert c.s.dataset_records.get_record_for_edit(c.a.id, c.d.key, c.record.id).revision == 1
    assert content(c, first) == before
    assert c.s.record_imports.eligibility(c.a.id, c.d.key)[0] == "Import already completed"
    with pytest.raises(NotFoundError):
        confirm(c, state)
    with c.sessions() as session:
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.action == "dataset.new_version_imported")
        ).all()
        assert len(events) == 1
        assert events[0].details == {
            "mode": "new_version",
            "format": "CSV",
            "added": 1,
            "changed": 0,
            "unchanged": 1,
            "deleted": 0,
            "total_processed": 2,
            "unmapped_fields": [],
        }


def test_deletion_acknowledgement_retention_undo_and_publication(context):
    c = context
    first, concept = clean(c)
    state, result = preview(c, b"code,label\nb,New\n")
    assert result.counts["Deleted"] == result.counts["Added"] == 1
    before = snapshot(c)
    with pytest.raises(ImportValidationError, match="exact number"):
        confirm(c, state)
    assert snapshot(c) == before
    c.s.record_imports.saved_preview(c.a.id, state.session_id, c.d.key, state.token)
    state = c.s.record_imports.state(c.a.id, state.session_id, c.d.key, state.token)
    confirm(c, state, acknowledge_deletions=True)
    view = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    deleted = next(r for r in view.page.records if r.id == c.record.id)
    assert view.record_states[deleted.id] == "Deleted"
    with c.sessions() as session:
        assert session.get(DatasetRecord, deleted.id).deleted_content is not None
    restored = c.s.dataset_records.restore_record(
        c.a.id, c.d.key, deleted.id, deleted.revision, concept_id=concept.id
    )
    assert restored.values == deleted.values
    with pytest.raises(NotFoundError):
        c.s.dataset_records.restore_record(
            c.a.id, c.d.key, deleted.id, deleted.revision, concept_id=concept.id
        )
    c.s.dataset_records.delete_record(c.a.id, c.d.key, restored.id, restored.revision)
    second = publish(c)
    assert len(content(c, first).records) == len(content(c, second).records) == 1
    assert content(c, first).records[0].id != content(c, second).records[0].id


@pytest.mark.parametrize("mutation", ["add", "edit", "delete", "restore", "create_import"])
def test_record_mutations_block_all_new_import_steps(database_context, mutation):
    c = database_context
    _, concept = clean(c)
    state, _ = preview(c)
    if mutation == "add":
        c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "new"})
    elif mutation == "edit":
        c.s.dataset_records.update_record(
            c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "edit"}
        )
        c.s.dataset_records.update_record(
            c.a.id, c.d.key, c.record.id, 2, {"code": "a", "label": "Café"}
        )
    elif mutation in {"delete", "restore"}:
        c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, 1)
        if mutation == "restore":
            c.s.dataset_records.restore_record(
                c.a.id, c.d.key, c.record.id, 2, concept_id=concept.id
            )
    else:
        ordinary, _, _ = c.s.record_imports.begin(
            c.a.id, "ordinary", c.d.key, "add.csv", b"code,label\nz,Z\n"
        )
        c.s.record_imports.preview(c.a.id, "ordinary", c.d.key, ordinary.token, ("code", "label"))
        c.s.record_imports.confirm(c.a.id, "ordinary", c.d.key, ordinary.token)
    assert "already been changed" in c.s.record_imports.eligibility(c.a.id, c.d.key)[1]
    for operation in (
        lambda: preview(c, session="other"),
        lambda: confirm(c, state),
        lambda: c.s.record_imports.saved_preview(c.a.id, "new", c.d.key, state.token),
    ):
        with pytest.raises(ImportValidationError, match="already been changed"):
            operation()


def test_import_unavailable_message_explains_complete_record_set(database_context):
    c = database_context
    clean(c)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Manual change"}
    )
    reason = c.s.record_imports.eligibility(c.a.id, c.d.key)
    assert reason[0] == "Import unavailable"
    message = reason[1]
    for explanation in (
        "For safety",
        "an import doesn't add Records",
        "the file describes the complete set of Records you want",
        "Added, Changed, Unchanged or Deleted",
        "Publish or discard this Draft",
    ):
        assert explanation in message

    browser, _ = dataset_version_client(c)
    records_page = request(browser, "get", f"/datasets/{c.d.key}/concept/records")
    assert records_page.status_code == 200
    assert "For safety" in records_page.text
    assert "Added, Changed, Unchanged or Deleted" in records_page.text

    import_page = request(browser, "get", f"/datasets/{c.d.key}/records/import")
    assert import_page.status_code == 400
    assert "For safety" in import_page.text
    assert "Added, Changed, Unchanged or Deleted" in import_page.text


@pytest.mark.parametrize("mutation", ["add", "edit", "delete", "restore"])
def test_unpublished_dataset_allows_import_after_record_mutations(database_context, mutation):
    c = database_context
    setup(c)
    if mutation == "add":
        c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "manual", "label": "Manual"})
    elif mutation == "edit":
        c.s.dataset_records.update_record(
            c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Manual"}
        )
    elif mutation in {"delete", "restore"}:
        with c.sessions() as session:
            concept_id = session.get(DatasetConcept, c.d.id).id
        c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, 1)
        if mutation == "restore":
            c.s.dataset_records.restore_record(
                c.a.id, c.d.key, c.record.id, 2, concept_id=concept_id
            )

    assert c.s.record_imports.eligibility(c.a.id, c.d.key) is None
    browser, _ = dataset_version_client(c)
    records_page = request(browser, "get", f"/datasets/{c.d.key}/concept/records")
    assert records_page.status_code == 200
    assert f"/datasets/{c.d.key}/records/import" in records_page.text
    import_page = request(browser, "get", f"/datasets/{c.d.key}/records/import")
    assert import_page.status_code == 200
    assert "Supported formats: .csv and .xlsx." in import_page.text

    state, result = preview(c, b"code,label\na,Cafe\n", session=f"unpublished-{mutation}")
    assert result.valid
    if mutation == "add":
        assert result.counts["Deleted"] == 1
        assert any(
            difference["identifier"] == "manual" and difference["status"] == "Deleted"
            for difference in result.differences
        )
    elif mutation == "edit":
        assert result.counts["Changed"] == 1
    confirm(c, state, acknowledge_deletions=bool(result.counts["Deleted"]))


def test_unpublished_dataset_can_be_imported_twice_and_keeps_only_latest_results(database_context):
    c = database_context
    setup(c)
    first, first_preview = preview(
        c, b"code,label\na,First import\nb,Added in first import\n", session="first"
    )
    assert first_preview.counts["Changed"] == 1
    assert first_preview.counts["Added"] == 1
    confirm(c, first)
    first_view = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    added = next(record for record in first_view.page.records if record.id != c.record.id)

    second, second_preview = preview(c, b"code,label\na,First import\n", session="second")
    assert second_preview.valid
    assert second_preview.counts["Unchanged"] == 1
    assert second_preview.counts["Deleted"] == 1
    assert second_preview.differences[0]["status"] == "Deleted"
    confirm(c, second, acknowledge_deletions=True)

    view = c.service.read(
        c.a.id,
        c.d.key,
        prefer_concept=True,
        statuses=("Added", "Changed", "Deleted"),
    )
    assert view.page.records == () and view.page.total == 0
    assert view.deleted_count == 0 and view.status_counts == (1, 0, 0)
    restored = c.s.dataset_records.restore_record(
        c.a.id, c.d.key, added.id, added.revision + 1, concept_id=view.concept.id
    )
    assert {value.field_key: value.value for value in restored.values}["code"] == "b"
    with c.sessions() as session:
        concept = session.get(DatasetConcept, c.d.id)
        assert concept.import_result["Changed"] == 0
        assert concept.import_result["Added"] == 0
        assert concept.import_result["Deleted"] == 1
        assert set(concept.import_result["records"].values()) == {"Deleted"}


def test_unpublished_import_confirmation_is_invalidated_when_version_is_published(context):
    c = context
    setup(c)
    state, _ = preview(c, b"code,label\na,Imported\n", session="staged-before-publish")
    publish(c)

    with pytest.raises(ConflictError):
        confirm(c, state)


def test_field_only_and_legacy_eligibility(context):
    c = context
    clean(c)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Renamed", "string", False, False)
    assert c.s.record_imports.eligibility(c.a.id, c.d.key) is None
    with c.sessions.begin() as session:
        session.get(DatasetConcept, c.d.id).records_mutated = None
    assert (
        "created before new-version imports" in c.s.record_imports.eligibility(c.a.id, c.d.key)[1]
    )
    with pytest.raises(ImportValidationError, match="created before"):
        preview(c)


def test_import_eligibility_without_active_concept_returns_conflict(context):
    c = context
    setup(c)
    publish(c)

    with pytest.raises(ConflictError, match="Start a New concept before editing this Dataset"):
        c.s.record_imports.eligibility(c.a.id, c.d.key)


@pytest.mark.parametrize("source", [b"code,label\na,One\na,Two\n", b"code,label\n,Missing\n"])
def test_invalid_file_never_applies(context, source):
    c = context
    clean(c)
    state, result = preview(c, source)
    assert not result.valid
    before = snapshot(c)
    with pytest.raises(ImportValidationError):
        confirm(c, state, acknowledge_deletions=True)
    assert snapshot(c) == before


def test_real_migration_preserves_legacy_deletion_and_blocks_only_old_concepts(database_context):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    c = database_context
    first, _ = clean(c)
    original = content(c, first)
    config = Config("alembic.ini")
    with c.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.stamp(config, "0021_ref_40")
        # stamp owns a transaction on PostgreSQL; commit before the migration boundary.
        connection.commit()
        command.downgrade(config, "0017_ref_154")
        connection.commit()
        connection.execute(
            text("DELETE FROM dataset_record_values WHERE record_id=:id"), {"id": c.record.id}
        )
        connection.execute(text("DELETE FROM dataset_records WHERE id=:id"), {"id": c.record.id})
        connection.commit()
        command.upgrade(config, "head")
        connection.commit()
    assert (
        "created before new-version imports" in c.s.record_imports.eligibility(c.a.id, c.d.key)[1]
    )
    with pytest.raises(ImportValidationError, match="created before"):
        preview(c)
    view = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    assert view.record_states[c.record.id] == "Deleted"
    assert {v.field_id: v.canonical_key for v in view.page.records[0].values} == {
        v.field_id: v.canonical_key for v in original.records[0].values
    }
    second = publish(c)
    assert not content(c, second).records
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Renamed", "string", False, False)
    assert c.s.record_imports.eligibility(c.a.id, c.d.key) is None


def test_complete_diff_pagination_search_and_no_preview_writes(context):
    c = context
    setup(c)
    removed = c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "gone", "label": "Old"})
    first = publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    original = content(c, first)
    before = snapshot(c)
    rows = b"code,label\na,Updated\n" + b"".join(
        f"new-{i:03d},New {i}\n".encode() for i in range(105)
    )
    state, result = preview(c, rows)
    assert snapshot(c) == before
    assert {k: result.counts[k] for k in ("Added", "Changed", "Deleted")} == {
        "Added": 105,
        "Changed": 1,
        "Deleted": 1,
    }
    assert len(result.differences) == 50
    second = c.s.record_imports.saved_preview(c.a.id, "new", c.d.key, state.token, page=2)
    third = c.s.record_imports.saved_preview(c.a.id, "new", c.d.key, state.token, page=3)
    assert len(second.differences) == 50 and len(third.differences) == 7
    assert len({d["identifier"] for p in (result, second, third) for d in p.differences}) == 107
    changed = c.s.record_imports.saved_preview(
        c.a.id, "new", c.d.key, state.token, statuses=("Changed",)
    )
    assert changed.counts["matching"] == 1
    assert changed.differences[0]["changes"] == (("Label", "Café", "Updated"),)
    searched = c.s.record_imports.saved_preview(
        c.a.id, "new", c.d.key, state.token, search="new-104"
    )
    assert searched.counts["matching"] == 1
    confirm(c, state, acknowledge_deletions=True)
    assert content(c, first) == original
    view = c.service.read(c.a.id, c.d.key, prefer_concept=True, statuses=("Changed", "Deleted"))
    assert {r.id for r in view.page.records} == {c.record.id, removed.id}
    assert c.s.dataset_records.get_record_for_edit(c.a.id, c.d.key, c.record.id).revision == 2


def test_same_changed_record_uses_changed_label_in_table_import_and_review(context):
    import re
    from io import BytesIO
    from urllib.parse import parse_qs, urlsplit

    from support import dataset_version_client as client
    from support import request

    c = context
    clean(c)
    browser, csrf = client(c)
    root = f"/datasets/{c.d.key}/records/import"
    entrypoint = request(browser, "get", root)
    assert entrypoint.status_code == 200, entrypoint.text
    upload = request(
        browser,
        "post",
        root,
        data={
            "csrf_token": csrf,
            "file": (BytesIO(b"code,label\na,Changed draft\n"), "records.csv"),
        },
    )
    assert upload.status_code == 303, upload.text
    token = upload.location.split("/import/")[1].split("/")[0]
    staged = f"/datasets/{c.d.key}/records/import/{token}"
    mapped = request(
        browser,
        "post",
        staged + "/preview",
        data={"csrf_token": csrf, "mapping_0": "code", "mapping_1": "label"},
    )
    assert mapped.status_code == 303
    preview_html = request(browser, "get", staged + "/step/4")
    assert preview_html.status_code == 200
    assert "<span class='badge badge-draft'>Changed</span>" in preview_html.text
    assert "Changed draft" in preview_html.text
    confirmation = re.search("name='confirmation' value='([^']+)'", preview_html.text)[1]
    imported = request(
        browser,
        "post",
        staged + "/confirm",
        data={"csrf_token": csrf, "confirmation": confirmation},
    )
    assert imported.status_code == 303

    records = request(browser, "get", f"/datasets/{c.d.key}/concept/records?record_status=all")
    assert "<span class='badge badge-draft'>Changed</span>" in records.text
    entry = request(browser, "get", f"/datasets/{c.d.key}/versions/review")
    review_token = parse_qs(urlsplit(entry.location).query)["review"][0]
    review = request(
        browser,
        "get",
        f"/datasets/{c.d.key}/versions/review?review={review_token}&section=Records",
    )
    assert review.status_code == 200
    assert "<span class='badge badge-draft'>Changed</span>" in review.text


@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_new_version_failure_rolls_back_all_content_marker_and_audit(
    database_context, monkeypatch, failure
):
    from sqlalchemy.orm import Session

    from refrncr.persistence import SqlAlchemyUnitOfWork

    c = database_context
    clean(c)
    state, _ = preview(c, b"code,label\nb,New\n")
    before = snapshot(c)

    def fail(*args, **kwargs):
        raise RuntimeError("injected")

    with monkeypatch.context() as patch:
        patch.setattr(
            SqlAlchemyUnitOfWork if failure == "audit" else Session,
            "add_audit" if failure == "audit" else "commit",
            fail,
        )
        with pytest.raises(RuntimeError, match="injected"):
            confirm(c, state, acknowledge_deletions=True)
    assert snapshot(c) == before
    assert c.s.record_imports.eligibility(c.a.id, c.d.key) is None
    assert confirm(c, state, acknowledge_deletions=True) == 1


def test_stale_schema_and_confirmation_require_review_and_fresh_ack(context):
    c = context
    clean(c)
    state, _ = preview(c, b"code,label\nb,New\n")
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Renamed", "string", False, False)
    with pytest.raises(ImportValidationError, match="schema changed"):
        confirm(c, state, acknowledge_deletions=True)
    c.s.record_imports.saved_preview(c.a.id, "new", c.d.key, state.token)
    fresh = c.s.record_imports.state(c.a.id, "new", c.d.key, state.token)
    assert fresh.confirmation != state.confirmation
    with pytest.raises(ImportValidationError, match="confirm again"):
        confirm(c, state, acknowledge_deletions=True)
    with pytest.raises(ImportValidationError, match="exact number"):
        confirm(c, fresh)
    confirm(c, fresh, acknowledge_deletions=True)


@pytest.mark.parametrize(
    "source,mapping",
    [
        (b"code,label\n", ("code", "label")),
        (b"code\na\n", ("code",)),
        (b"code,label\na,New\n", (None, "label")),
    ],
)
def test_empty_file_and_missing_identifier_field_are_blocked(database_context, source, mapping):
    c = database_context
    clean(c)
    before = snapshot(c)
    try:
        state, _, _ = c.s.record_imports.begin(
            c.a.id, "new", c.d.key, "records.csv", source, mode="new_version"
        )
    except ImportValidationError:
        assert snapshot(c) == before
        return
    result = c.s.record_imports.preview(c.a.id, "new", c.d.key, state.token, mapping)
    assert not result.valid
    with pytest.raises((ImportValidationError, NotFoundError)):
        confirm(c, state, acknowledge_deletions=True)
    assert snapshot(c) == before


def test_concurrent_independent_staged_confirms_commit_once(database_context):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    c = database_context
    clean(c)
    one, _ = preview(c, session="one")
    two, _ = preview(c, session="two")
    gate = Barrier(2)

    def attempt(state):
        gate.wait(timeout=10)
        try:
            return confirm(c, state)
        except ImportValidationError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (one, two)))
    assert sorted(str(r) for r in results) == ["2", "blocked"]
    with c.sessions() as session:
        assert (
            len(
                session.scalars(
                    select(AuditEvent).where(AuditEvent.action == "dataset.new_version_imported")
                ).all()
            )
            == 1
        )


def test_composite_identifier_and_xlsx_canonical_source_formats(context):
    from io import BytesIO

    from openpyxl import Workbook

    c = context
    setup(c, populated=False)
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "sequence", "Sequence", "integer", True)
    c.s.dataset_business_keys.set_business_key(c.a.id, c.d.key, ("sequence", "code"))
    original = c.s.dataset_records.create_record(
        c.a.id, c.d.key, {"code": "a", "sequence": "1", "label": "Café"}
    )
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    book = Workbook()
    book.active.title = "Complete"
    book.active.append(["code", "label", "sequence"])
    book.active.append(["a", "Cafe\u0301", 1])
    book.active.append(["b", "Added", 2])
    stream = BytesIO()
    book.save(stream)
    book.close()
    state, _, _ = c.s.record_imports.begin(
        c.a.id, "xlsx", c.d.key, "complete.xlsx", stream.getvalue(), mode="new_version"
    )
    result = c.s.record_imports.preview(
        c.a.id, "xlsx", c.d.key, state.token, ("code", "label", "sequence")
    )
    assert result.valid and result.counts["Unchanged"] == 1 and result.counts["Added"] == 1
    state = c.s.record_imports.state(c.a.id, "xlsx", c.d.key, state.token)
    confirm(c, state)
    assert c.s.dataset_records.get_record_for_edit(c.a.id, c.d.key, original.id).revision == 1


def test_unique_values_can_swap_and_be_reused_from_deleted_records(database_context):
    c = database_context
    setup(c)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Label", "string", False, True)
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "b", "label": "Second"})
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "c", "label": "Third"})
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    state, result = preview(c, "code,label\na,Second\nb,Café\nd,Third\n".encode())
    assert result.valid and result.counts["Changed"] == 2
    confirm(c, state, acknowledge_deletions=True)
    view = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    assert len(view.page.records) == 4 and view.deleted_count == 1
    deleted = next(r for r in view.page.records if view.record_states[r.id] == "Deleted")
    before = snapshot(c)
    from refrncr.services import RecordConflictError

    with pytest.raises(RecordConflictError, match="unique"):
        c.s.dataset_records.restore_record(
            c.a.id, c.d.key, deleted.id, deleted.revision, concept_id=view.concept.id
        )
    assert snapshot(c) == before


@pytest.mark.parametrize("empty", [True, False])
@pytest.mark.parametrize("legacy_address", [False, True])
def test_one_http_import_flow_for_empty_and_populated_tables(context, empty, legacy_address):
    import io
    import re

    from support import dataset_version_client as client
    from support import request

    c = context
    if empty:
        setup(c, populated=False)
    else:
        clean(c)
    browser, csrf = client(c)
    root = f"/datasets/{c.d.key}/" + (
        "concept/records/import-new-version" if legacy_address else "records/import"
    )
    entry = request(browser, "get", root)
    assert entry.status_code == 200
    assert "Import records</h1>" in entry.text
    assert "file defines the complete desired set" in entry.text
    assert "Add records from file" not in entry.text
    uploaded = request(
        browser,
        "post",
        root,
        data={
            "csrf_token": csrf,
            "file": (io.BytesIO(b"code,label\na,Updated\nb,Added\n"), "complete.csv"),
        },
    )
    assert uploaded.status_code == 303
    token = uploaded.location.split("/import/")[1].split("/")[0]
    staged = f"/datasets/{c.d.key}/records/import/{token}"
    mapped = request(
        browser,
        "post",
        staged + "/preview",
        data={
            "csrf_token": csrf,
            "mapping_0": "code",
            "mapping_1": "label",
        },
    )
    assert mapped.status_code == 303
    final = request(browser, "get", staged + "/step/4")
    assert final.status_code == 200
    assert "name='acknowledge_deletions'" not in final.text
    confirmation = re.search("name='confirmation' value='([^']+)'", final.text)[1]
    result = request(
        browser,
        "post",
        staged + "/confirm",
        data={
            "csrf_token": csrf,
            "confirmation": confirmation,
        },
    )
    assert result.status_code == 303 and "import_results=1" in result.location
    with c.sessions() as session:
        receipt = session.get(DatasetConcept, c.d.id).import_result
        assert receipt["Added"] == (2 if empty else 1)
        assert receipt["Changed"] == (0 if empty else 1)
        assert receipt["Deleted"] == 0
    # A no-baseline Dataset remains eligible; a published Dataset keeps the one-import guard.
    assert request(browser, "get", root + "?mode=create").status_code == (200 if empty else 400)


def test_http_status_checkboxes_and_all_records(context):
    from support import dataset_version_client as client
    from support import request

    c = context
    clean(c)
    state, _ = preview(c, "code,label\na,Cafe\u0301\nb,New\n".encode())
    confirm(c, state)
    browser, _ = client(c)
    root = f"/datasets/{c.d.key}/concept/records"
    all_changes = request(browser, "get", root + "?import_results=1")
    assert all_changes.status_code == 200
    assert b"Change status: All changes" in all_changes.data
    assert all_changes.data.count(b" checked") == 3
    multiple = request(
        browser, "get", root + "?import_results=1&status_selection=1&status=Added&status=Changed"
    )
    assert multiple.status_code == 200
    assert b"Change status: 2 statuses" in multiple.data
    assert multiple.data.count(b" checked") == 2
    assert b"Showing 1\xe2\x80\x931 of 1" in multiple.data
    no_selection = request(browser, "get", root + "?import_results=1&status_selection=1")
    assert no_selection.status_code == 200
    assert b"Change status: All records" in no_selection.data
    assert b" checked" not in no_selection.data
    assert b"Showing 1\xe2\x80\x932 of 2" in no_selection.data
    unfiltered = request(browser, "get", root)
    assert b"Change status: All records" in unfiltered.data
    assert b"Clear import-results filter" not in unfiltered.data


@pytest.mark.slow
def test_http_security_confirmation_binding_and_undo(context):
    import io
    import re

    from support import dataset_version_client as client
    from support import request
    from werkzeug.datastructures import MultiDict

    c = context
    _, concept = clean(c)
    browser, csrf = client(c)
    root = f"/datasets/{c.d.key}/records/import"
    ordinary, _ = client(c, c.u)
    assert request(ordinary, "get", root).status_code == 404
    assert (
        request(
            browser,
            "post",
            root,
            data={"file": (io.BytesIO(b"code,label\nb,New\n"), "records.csv")},
        ).status_code
        == 403
    )
    upload = request(
        browser,
        "post",
        root,
        data={
            "csrf_token": csrf,
            "file": (io.BytesIO(b"code,label\nb,<script>alert(1)</script>\n"), "records.csv"),
        },
    )
    assert upload.status_code == 303
    mapping_url = upload.location
    token = mapping_url.split("/import/")[1].split("/")[0]
    staged = f"/datasets/{c.d.key}/records/import/{token}"
    mapped = request(
        browser,
        "post",
        staged + "/preview",
        data={"csrf_token": csrf, "mapping_0": "code", "mapping_1": "label"},
    )
    assert mapped.status_code == 303
    page = request(browser, "get", staged + "/step/4")
    assert page.status_code == 200 and "import-record-" in page.text
    assert "&lt;script&gt;" in page.text and "<script>" not in page.text
    nonce = re.search("name='confirmation' value='([^']+)'", page.text)[1]
    other_session, other_csrf = client(c)
    before = snapshot(c)
    confirmation = {"csrf_token": csrf, "confirmation": nonce}
    assert request(browser, "post", staged + "/confirm", data=confirmation).status_code == 409
    confirmation["acknowledge_deletions"] = "yes"
    assert (
        request(
            other_session,
            "post",
            staged + "/confirm",
            data=confirmation | {"csrf_token": other_csrf},
        ).status_code
        == 404
    )
    duplicate = MultiDict(confirmation)
    duplicate.add("confirmation", nonce)
    assert request(browser, "post", staged + "/confirm", data=duplicate).status_code == 400
    assert snapshot(c) == before
    # A rejected confirmation invalidates validation; refresh the preview.
    request(browser, "get", staged + "/step/4")
    page = request(browser, "get", staged + "/step/4")
    confirmation["confirmation"] = re.search("name='confirmation' value='([^']+)'", page.text)[1]
    result = request(browser, "post", staged + "/confirm", data=confirmation)
    assert result.status_code == 303 and "import_results=1" in result.location
    assert request(browser, "post", staged + "/confirm", data=confirmation).status_code == 404
    table = request(browser, "get", result.location)
    assert (
        "&lt;script&gt;alert(1)&lt;/script&gt;" in table.text and "<script>alert" not in table.text
    )
    restore = f"/datasets/{c.d.key}/concept/records/{c.record.id}/restore"
    payload = {"csrf_token": csrf, "concept_id": concept.id, "expected_revision": "2"}
    assert request(ordinary, "post", restore, data=payload).status_code in {403, 404}
    assert (
        request(browser, "post", restore, data=payload | {"expected_revision": "1"}).status_code
        == 409
    )
    assert request(browser, "post", restore, data=payload).status_code == 303
    assert request(browser, "post", restore, data=payload).status_code == 404


def test_manual_deleted_added_record_retains_last_concept_values(context):
    c = context
    first, concept = clean(c)
    current = content(c, first)
    added = c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "draft", "label": "First"})
    edited = c.s.dataset_records.update_record(
        c.a.id, c.d.key, added.id, 1, {"code": "draft", "label": "Last Concept value"}
    )
    c.s.dataset_records.delete_record(c.a.id, c.d.key, added.id, 2)
    view = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    assert added.id not in view.record_states
    assert view.deleted_count == 0
    restored = c.s.dataset_records.restore_record(
        c.a.id, c.d.key, added.id, 3, concept_id=concept.id
    )
    assert restored.values == edited.values and restored.revision == 4
    assert content(c, first) == current


def test_legacy_marker_survives_field_and_record_changes(context):
    c = context
    clean(c)
    with c.sessions.begin() as session:
        session.get(DatasetConcept, c.d.id).records_mutated = None
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Legacy Field", "string", False, False
    )
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "legacy"})
    assert (
        "created before new-version imports" in c.s.record_imports.eligibility(c.a.id, c.d.key)[1]
    )


def test_noop_record_edit_does_not_invent_a_mutation(context):
    c = context
    clean(c)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Café"}
    )
    assert c.s.record_imports.eligibility(c.a.id, c.d.key) is None


def test_migration_failure_rolls_back_schema_marker_and_retained_rows(
    database_context, monkeypatch
):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text
    from sqlalchemy.engine import Connection

    c = database_context
    clean(c)
    config = Config("alembic.ini")
    with c.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.stamp(config, "0021_ref_40")
        connection.commit()
        command.downgrade(config, "0017_ref_154")
        connection.commit()
        connection.execute(
            text("DELETE FROM dataset_record_values WHERE record_id=:id"), {"id": c.record.id}
        )
        connection.execute(text("DELETE FROM dataset_records WHERE id=:id"), {"id": c.record.id})
        connection.commit()
        original = Connection.execute

        def fail(self, statement, *args, **kwargs):
            if str(statement).startswith("INSERT INTO dataset_records"):
                raise RuntimeError("injected migration copy failure")
            return original(self, statement, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Connection, "execute", fail)
            with pytest.raises(RuntimeError, match="injected migration"):
                command.upgrade(config, "head")
        connection.rollback()
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0017_ref_154"
        assert "deleted_content" not in {
            v["name"] for v in inspect(connection).get_columns("dataset_records")
        }
        assert "records_mutated" not in {
            v["name"] for v in inspect(connection).get_columns("dataset_concepts")
        }
        connection.rollback()
        command.upgrade(config, "head")
        connection.commit()
    assert (
        "created before new-version imports" in c.s.record_imports.eligibility(c.a.id, c.d.key)[1]
    )


@pytest.mark.parametrize(
    "data_type,stored,source",
    [
        ("integer", "1", "001"),
        ("number", "1.25", "1.2500"),
        ("boolean", "true", "true"),
        ("date", "2026-09-13", "2026-09-13"),
        ("time", "12:30:00.1", "12:30:00.100000"),
        ("datetime", "2026-09-13T12:30:00.1", "2026-09-13T12:30:00.100000"),
        ("timestamp", "2026-09-13T12:30:00Z", "2026-09-13T14:30:00+02:00"),
    ],
)
def test_typed_identifier_equivalence_is_unchanged(context, data_type, stored, source):
    c = context
    setup(c, populated=False)
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "part", "Part", data_type, True)
    c.s.dataset_business_keys.set_business_key(c.a.id, c.d.key, ("part",))
    record = c.s.dataset_records.create_record(
        c.a.id, c.d.key, {"part": stored, "code": "a", "label": "Same"}
    )
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    state, _, _ = c.s.record_imports.begin(
        c.a.id,
        "typed",
        c.d.key,
        "typed.csv",
        f"code,label,part\na,Same,{source}\n".encode(),
        mode="new_version",
    )
    result = c.s.record_imports.preview(
        c.a.id, "typed", c.d.key, state.token, ("code", "label", "part")
    )
    assert result.valid and result.counts["Unchanged"] == 1 and result.counts["Changed"] == 0
    state = c.s.record_imports.state(c.a.id, "typed", c.d.key, state.token)
    confirm(c, state)
    assert c.s.dataset_records.get_record_for_edit(c.a.id, c.d.key, record.id).revision == 1


def test_empty_xlsx_and_no_active_concept_are_blocked(database_context):
    from io import BytesIO

    from openpyxl import Workbook

    from refrncr.services import ConflictError

    c = database_context
    clean(c)
    book = Workbook()
    book.active.append(["code", "label"])
    stream = BytesIO()
    book.save(stream)
    book.close()
    with pytest.raises(ImportValidationError):
        c.s.record_imports.begin(
            c.a.id, "empty", c.d.key, "empty.xlsx", stream.getvalue(), mode="new_version"
        )
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Changed label", "string", False, False
    )
    publish(c)
    with pytest.raises(ConflictError):
        preview(c)


def test_new_mode_cancel_expiry_and_replaced_concept_revoke_staged_content(context):
    from refrncr.record_import import TransientImportStore

    c = context
    clean(c)
    clock = [0.0]
    c.s.record_imports.store = TransientImportStore(clock=lambda: clock[0])
    state, _ = preview(c)
    c.s.record_imports.cancel(c.a.id, "new", c.d.key, state.token)
    with pytest.raises(NotFoundError):
        confirm(c, state)
    state, _ = preview(c)
    clock[0] += 901
    with pytest.raises(NotFoundError):
        confirm(c, state)
    state, _ = preview(c)
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Changed label", "string", False, False
    )
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    with pytest.raises(NotFoundError):
        confirm(c, state)
    assert c.s.record_imports.eligibility(c.a.id, c.d.key) is None


@pytest.mark.parametrize("source", [b"code,label\na,Changed\n", b"code,label\nb,Added\n"])
def test_mutating_an_exhausted_record_revision_fails_before_writes(context, source):
    from refrncr.services import MAX_RECORD_REVISION, ValidationError

    c = context
    clean(c)
    with c.sessions.begin() as session:
        session.get(DatasetRecord, c.record.id).revision = MAX_RECORD_REVISION
    before = snapshot(c)
    with pytest.raises(ImportValidationError, match="revision limit"):
        preview(c, source)
    with pytest.raises(ValidationError, match="out of range"):
        c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, MAX_RECORD_REVISION)
    assert snapshot(c) == before

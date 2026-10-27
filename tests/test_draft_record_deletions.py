"""Draft deletion counts reflect the publication baseline, including after import."""

import re

import pytest
from support import dataset_version_client, html_text, publish, request, setup

from refrncr.dataset_diff import DatasetDiffService
from refrncr.record_import import ImportValidationError


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("method", ["manual", "import"])
def test_draft_deletion_counts_match_review(database_context, published, method):
    c = database_context
    setup(c)
    if published:
        publish(c)
        c.service.create_concept(c.a.id, c.d.id)
    if method == "import":
        importer = c.s.record_imports
        state, _, _ = importer.begin(
            c.a.id,
            "deletion-counts",
            c.d.key,
            "records.csv",
            b"code,label\nb,New\n",
            mode="new_version",
        )
        preview = importer.preview(
            c.a.id, state.session_id, c.d.key, state.token, ("code", "label")
        )
        assert preview.valid and preview.counts["Deleted"] == 1
        state = importer.state(c.a.id, state.session_id, c.d.key, state.token)
        with pytest.raises(
            ImportValidationError,
            match="Confirm the exact number",
        ):
            importer.confirm(
                c.a.id,
                state.session_id,
                c.d.key,
                state.token,
                confirmation=state.confirmation,
            )
        # A rejected confirmation invalidates its preview; review the same source again.
        importer.preview(c.a.id, state.session_id, c.d.key, state.token, ("code", "label"))
        state = importer.state(c.a.id, state.session_id, c.d.key, state.token)
        importer.confirm(
            c.a.id,
            state.session_id,
            c.d.key,
            state.token,
            confirmation=state.confirmation,
            acknowledge_deletions=True,
        )
    else:
        c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "b", "label": "New"})
        c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, 1)

    deleted = int(published)
    view = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    review = DatasetDiffService(c.service).open_review(c.a.id, c.d.id)
    assert view.context.record_count == review.counts.records_added == 1
    assert view.deleted_count == review.counts.records_removed == deleted
    assert view.status_counts == (1, 0, deleted)
    assert view.page.total == 1 + deleted
    assert (c.record.id in {row.id for row in view.page.records}) == published
    filtered = c.service.read(c.a.id, c.d.key, prefer_concept=True, record_status="deleted")
    assert filtered.page.total == deleted

    browser, _ = dataset_version_client(c)
    root = f"/datasets/{c.d.key}/concept/records"
    page = request(browser, "get", root)
    assert page.status_code == 200
    visible = html_text(page.text)
    assert f"Showing 1–{1 + deleted} of {1 + deleted}" in visible
    assert ("1 marked for deletion" in visible) == published
    assert (f"record-row-{c.record.id}" in page.text) == published
    assert re.search(rf"Deleted\s*<span class='status-toggle-count'>{deleted}</span>", page.text)
    deleted_page = request(browser, "get", root + "?record_status=deleted")
    assert deleted_page.status_code == 200
    assert (f"record-row-{c.record.id}" in deleted_page.text) == published
    assert f"Showing {1 if deleted else 0}–{deleted} of {deleted}" in html_text(deleted_page.text)

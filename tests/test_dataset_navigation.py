"""Dataset tabs preserve draft context and identify non-current versions."""

import re
from dataclasses import replace
from datetime import timedelta
from html import unescape

import pytest
from support import NOW, configured, dataset_version_client, publish, request

from refrncr.models import DatasetConcept
from refrncr.services import NotFoundError


def records_link(page):
    navigation = re.search(r"<nav\b[^>]*aria-label='Dataset sections'.*?</nav>", page, re.S)
    assert navigation
    return unescape(re.search(r"href='([^']+)'[^>]*>Records</a>", navigation.group())[1])


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("tab", ["fields", "access-management", "dataset-details"])
def test_configuration_tabs_preserve_draft_context(context, published, tab):
    c = context
    configured(c)
    if published:
        publish(c)
        c.service.create_concept(c.a.id, c.d.id)
        c.s.dataset_records.update_record(
            c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Draft only value"}
        )
    browser, _ = dataset_version_client(c)
    page = request(browser, "get", f"/datasets/{c.d.key}/concept?tab={tab}#{tab}")
    assert page.status_code == 200
    assert "aria-label='Draft context'" in page.text
    link = records_link(page.text)
    assert link == f"/datasets/{c.d.key}/concept/records"
    records = request(browser, "get", link)
    assert records.status_code == 200
    assert "aria-label='Draft context'" in records.text
    assert ("Draft only value" if published else "Café") in records.text
    assert f"Records · {c.d.name} · REFRNCR" in records.text


@pytest.mark.parametrize("tab", ["records", "fields", "access-management", "dataset-details"])
@pytest.mark.parametrize("status", ["Scheduled", "Expired"])
def test_version_status_bar_is_shared_by_dataset_tabs(context, tab, status):
    c = context
    configured(c)
    if status == "Scheduled":
        version = publish(c, day=NOW.date() + timedelta(days=1))
    else:
        version = publish(c)
        c.service.create_concept(c.a.id, c.d.id)
        c.s.dataset_records.update_record(
            c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Successor"}
        )
        publish(c)
    browser, _ = dataset_version_client(c)
    root = f"/datasets/{c.d.key}/versions/{version.id}"
    page = request(browser, "get", root + ("/records" if tab == "records" else f"?tab={tab}"))
    assert page.status_code == 200
    assert f"aria-label='{status} version'" in page.text
    explanation = (
        "You're viewing an expired version"
        if status == "Expired"
        else "You're viewing a scheduled version"
    )
    assert explanation in page.text
    assert "View current version" in page.text
    assert "aria-label='Draft context'" not in page.text
    assert records_link(page.text) == root + "/records"
    if status == "Scheduled":
        assert "version-status-bar-info" in page.text
        assert "Cancel scheduled version…" in page.text
        assert f"href='{root}/cancel'" in page.text
    else:
        assert "version-status-bar-neutral" in page.text
        assert "in effect from" in page.text
        assert "Cancel scheduled version" not in page.text


@pytest.mark.parametrize("state", ["Draft", "Scheduled", "Cancelled"])
def test_records_without_current_redirect_to_available_context(context, state):
    c = context
    configured(c)
    if state != "Draft":
        version = publish(c, day=NOW.date() + timedelta(days=1))
        if state == "Cancelled":
            c.service.cancel(c.a.id, c.d.id, version.id)
    browser, _ = dataset_version_client(c)
    response = request(browser, "get", f"/datasets/{c.d.key}/records")
    expected = {
        "Draft": f"/datasets/{c.d.key}/concept/records",
        "Scheduled": f"/datasets/{c.d.key}/versions/{version.id}/records"
        if state != "Draft"
        else "",
        "Cancelled": f"/datasets/{c.d.key}/versions",
    }[state]
    assert response.status_code == 303
    assert response.location == expected
    destination = request(browser, "get", response.location)
    assert destination.status_code == 200
    assert "No current version" not in destination.text
    if state == "Scheduled":
        assert "aria-label='Scheduled version'" in destination.text
    elif state == "Draft":
        assert "aria-label='Draft context'" in destination.text
    else:
        assert "Cancelled" in destination.text and "Café" not in destination.text
        assert (
            request(
                browser, "get", f"/datasets/{c.d.key}/versions/{version.id}/records"
            ).status_code
            == 404
        )


def test_draft_takes_precedence_over_scheduled_when_current_is_absent(context):
    c = context
    configured(c)
    publish(c, day=NOW.date() + timedelta(days=1))
    c.service.create_concept(c.a.id, c.d.id)
    browser, _ = dataset_version_client(c)
    page = request(browser, "get", f"/datasets/{c.d.key}/records")
    assert page.status_code == 303
    assert page.location == f"/datasets/{c.d.key}/concept/records"


@pytest.mark.parametrize("maintainer", [True, False])
def test_empty_records_page_without_draft_or_versions(context, maintainer):
    c = context
    c.service = c.s.dataset_versions
    # A legacy empty Dataset can exist without the automatically created first Draft.
    with c.sessions.begin() as session:
        session.delete(session.get(DatasetConcept, c.d.id))
    browser, _ = dataset_version_client(c, c.a if maintainer else c.u)
    page = request(browser, "get", f"/datasets/{c.d.key}/records")
    if not maintainer:
        assert page.status_code == 404
        assert c.d.name not in page.text and "Create draft" not in page.text
        return
    assert page.status_code == 200
    assert f"Records · {c.d.name} · REFRNCR" in page.text
    for value in (c.d.name, c.d.key, "Reference dataset", c.owner.name):
        assert value in page.text
    assert records_link(page.text) == f"/datasets/{c.d.key}/records"
    for tab in ("Records", "Fields", "Access", "Settings", "Versions"):
        assert f">{tab}</a>" in page.text
    assert "This dataset has no records yet. Create a draft to add fields and records." in page.text
    assert "Create draft</button>" in page.text
    assert "name='csrf_token'" in page.text
    assert "aria-label='Draft context'" not in page.text


@pytest.mark.parametrize("state", ["Draft", "Scheduled", "Cancelled"])
def test_no_current_fallback_keeps_unpublished_datasets_private(context, state):
    c = context
    configured(c)
    if state != "Draft":
        version = publish(c, day=NOW.date() + timedelta(days=1))
        if state == "Cancelled":
            c.service.cancel(c.a.id, c.d.id, version.id)
    browser, _ = dataset_version_client(c, c.u)
    response = request(browser, "get", f"/datasets/{c.d.key}/records")
    assert response.status_code == 404
    assert "Location" not in response.headers
    assert c.d.name not in response.text and "Café" not in response.text
    assert "Create draft" not in response.text


def test_no_current_fallback_selects_latest_expired_snapshot(context, monkeypatch):
    c = context
    configured(c)
    expired = []
    for number in range(3):
        if number:
            c.service.create_concept(c.a.id, c.d.id)
            row = c.s.dataset_records.get_record_for_edit(c.a.id, c.d.key, c.record.id)
            c.s.dataset_records.update_record(
                c.a.id, c.d.key, row.id, row.revision, {"code": "a", "label": f"Version {number}"}
            )
        expired.append(publish(c))
    history = c.service.history(c.a.id, c.d.key)
    original_read = c.service.read

    def no_current(actor, key, version_id=None, *args, **kwargs):
        if version_id is None:
            raise NotFoundError("No Current version")
        return original_read(actor, key, version_id, *args, **kwargs)

    # The current lifecycle always retains a Current successor. Exercise the requested
    # expired-only fallback at the service boundary, using real historical snapshots.
    monkeypatch.setattr(c.service, "read", no_current)
    monkeypatch.setattr(
        c.service,
        "history",
        lambda *_: replace(
            history, entries=tuple(state for state in history.entries if state.status == "Expired")
        ),
    )
    browser, _ = dataset_version_client(c)
    response = request(browser, "get", f"/datasets/{c.d.key}/records")
    assert response.status_code == 303
    assert response.location == f"/datasets/{c.d.key}/versions/{expired[-2].id}/records"
    page = request(browser, "get", response.location)
    assert page.status_code == 200
    assert "aria-label='Expired version'" in page.text
    assert "Version 1" in page.text

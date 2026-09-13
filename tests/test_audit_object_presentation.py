"""Existence, absent names and resolver failures have different audit presentations."""

import re
from dataclasses import replace
from uuid import uuid4

import pytest
from markupsafe import escape
from sqlalchemy import select
from test_audit_coverage import audit_db as audit_db
from test_audit_history import client, seed, state
from test_audit_history import history as history

from refrncr.audit import EVENTS, AuditContractError
from refrncr.audit_presentation import (
    ACTION_LABELS,
    ACTION_PRESENTATIONS,
    OBJECTS,
    ActionPresentation,
    present_object,
    require_complete_presentation,
)
from refrncr.models import AuditEvent, DatasetExportAccess, DatasetRecord, User
from refrncr.persistence import SqlAlchemyAuditReadOperations
from refrncr.ports import AUDIT_NAMED_OBJECT_TYPES, AuditObjectRecord, AuditReadUnavailable


@pytest.mark.parametrize("name", [None, "", "   ", "\u202e\x00", "x" * 201])
def test_existing_object_without_safe_name_is_not_deleted(name):
    result = present_object(AuditObjectRecord(True, name))
    assert (result.status, result.label) == ("unnamed", "No name to display")
    assert present_object(AuditObjectRecord(False)).status == "deleted"


@pytest.mark.parametrize("name", ["Current name", "", '<img src=x onerror="alert(1)">'])
def test_list_and_detail_share_resolved_or_unnamed_status(history, name):
    h = history
    with h.sessions.begin() as session:
        session.get(User, h.reader).display_name = name
    eid = seed(h, 1)[0]
    browser = client(h, h.admin)
    before = state(h)
    view = h.service.get_event(h.admin, eid)
    assert view == h.service.list_history(h.admin).items[0]
    assert view.object_status == ("resolved" if name else "unnamed")
    assert view.object_label == (name or "No name to display")
    listing = browser.get("/settings/audit-log", base_url="https://localhost").get_data(
        as_text=True
    )
    detail = browser.get(f"/settings/audit-log/{eid}", base_url="https://localhost").get_data(
        as_text=True
    )
    assert f"<bdi>{escape(view.object_label)}" in listing
    # Jinja also escapes quotes; compare a non-markup name or the escaped img prefix.
    assert ("&lt;img" if name.startswith("<") else view.object_label) in detail
    assert "<img src=x" not in listing + detail
    assert h.reader not in listing and h.reader in detail
    assert state(h) == before


@pytest.mark.parametrize("kind", sorted(AUDIT_NAMED_OBJECT_TYPES))
def test_named_resolver_explicitly_proves_missing_object(history, kind):
    h = history
    eid = seed(h, 1, kind=kind, target=str(uuid4()))[0]
    with h.sessions() as session:
        result = SqlAlchemyAuditReadOperations(session).resolve_objects(
            kind, (h.service.get_event(h.admin, eid).object_id,)
        )
        assert list(result.values()) == [AuditObjectRecord(False)]
    view = h.service.get_event(h.admin, eid)
    assert (view.object_status, view.object_label) == ("deleted", "Deleted object")
    assert view == h.service.list_history(h.admin).items[0]


@pytest.mark.parametrize(
    "kind", ["dataset_record", "dataset_export_access", "membership", "grant", "unknown"]
)
def test_no_resolver_never_implies_deletion(history, kind):
    h = history
    eid = seed(h, 1, kind=kind)[0]
    view = h.service.get_event(h.admin, eid)
    assert (view.object_status, view.object_label) == ("unnamed", "No name to display")
    assert view.object_id == h.reader


@pytest.mark.parametrize("failure", ["exception", "incomplete"])
def test_resolver_failure_is_neutral_and_diagnostic(history, monkeypatch, caplog, failure):
    h = history
    eid = seed(h, 1)[0]
    browser = client(h, h.admin)
    before = state(h)

    def broken(*_args):
        if failure == "incomplete":
            return {}
        raise RuntimeError("secret SQL password and business payload")

    monkeypatch.setattr(SqlAlchemyAuditReadOperations, "resolve_objects", broken)
    with pytest.raises(AuditReadUnavailable):
        h.service.get_event(h.admin, eid)
    for path in ("/settings/audit-log", f"/settings/audit-log/{eid}"):
        response = browser.get(path, base_url="https://localhost")
        assert response.status_code == 503
        assert b"Deleted object" not in response.data and b"No name to display" not in response.data
        assert b"secret" not in response.data
    assert "Audit object resolution failed" in caplog.text and "secret" not in caplog.text
    assert state(h) == before


def test_guard_rejects_resolver_without_adapter_support():
    objects = dict(OBJECTS)
    objects["dataset_record"] = replace(objects["dataset_record"], resolver="dataset_record")
    with pytest.raises(AuditContractError, match="resolver"):
        require_complete_presentation(objects=objects)


def test_live_unnamed_objects_and_new_import_preserve_historical_events(history):
    h = history
    s, a = h.s, h.admin
    team = s.teams.create_team(a, "Domain")
    dataset = s.datasets.create_dataset(
        a, "auditnames", "Named Dataset", "Secret description", team.id
    )
    s.dataset_fields.create_field(a, dataset.key, "code", "Code", "string", True)
    s.dataset_business_keys.set_business_key(a, dataset.key, ("code",))
    record = s.dataset_records.create_record(a, dataset.key, {"code": "secret-manual"})
    access = s.datasets.grant_export_access(a, dataset.key, "user", h.reader)
    # Simulate retained per-record history from old imports; never rewrite it.
    seed(h, 1, kind="dataset_record", target=record.id)
    before = state(h)["audit_events"]
    with h.sessions() as session:
        original = {row.id for row in session.scalars(select(AuditEvent))}
    importer = s.record_imports
    staged, _, _ = importer.begin(
        a,
        "trusted",
        dataset.key,
        "test.csv",
        b"code,ignored\nsecret-import-a,x\nsecret-import-b,y\n",
    )
    assert importer.preview(a, "trusted", dataset.key, staged.token, ("code", None)).valid
    assert importer.confirm(a, "trusted", dataset.key, staged.token) == 2
    with h.sessions() as session:
        events = list(session.scalars(select(AuditEvent)))
        new = [row for row in events if row.id not in original]
        assert [(row.object_type, row.action) for row in new] == [
            ("dataset", "dataset.records_imported")
        ]
        assert new[0].details == {"format": "CSV", "record_count": 2, "rejected_row_count": 0}
        assert session.get(DatasetRecord, record.id) is not None
        assert session.get(DatasetExportAccess, access.id) is not None
    assert set(before) <= set(state(h)["audit_events"])
    before_manual = {row.id for row in events}
    second = s.dataset_records.create_record(a, dataset.key, {"code": "secret-manual-two"})
    with h.sessions() as session:
        manual = [row for row in session.scalars(select(AuditEvent)) if row.id not in before_manual]
        assert [(row.object_type, row.action, row.object_id) for row in manual] == [
            ("dataset_record", "created", second.id)
        ]
    before_reads = state(h)
    views = h.service.list_history(a).items
    for target in (record.id, second.id, access.id):
        view = next(row for row in views if row.object_id == target)
        assert (view.object_status, view.object_label) == ("unnamed", "No name to display")
        detail = h.service.get_event(a, view.id)
        assert (detail.object_status, detail.object_label, detail.object_id) == (
            view.object_status,
            view.object_label,
            view.object_id,
        )
    assert all("secret" not in row.object_label for row in views)
    assert state(h) == before_reads

    s.dataset_records.delete_record(a, dataset.key, second.id, 1)
    with h.sessions() as session:
        assert session.get(DatasetRecord, second.id).deleted_content is not None
    deleted = next(row for row in h.service.list_history(a).items if row.action == "deleted")
    assert (deleted.object_status, deleted.object_label) == ("deleted", "Deleted object")
    assert h.service.get_event(a, deleted.id) == deleted
    browser = client(h, a)
    unchanged = state(h)
    listing = browser.get("/settings/audit-log", base_url="https://localhost").get_data(
        as_text=True
    )
    row = next(row for row in re.findall(r"<tr>.*?</tr>", listing, re.S) if deleted.id in row)
    assert "Deleted object" in row and second.id not in row
    detail = browser.get(
        f"/settings/audit-log/{deleted.id}", base_url="https://localhost"
    ).get_data(as_text=True)
    assert "<dd><bdi>Deleted object</bdi></dd>" in detail and second.id in detail
    assert "data-side-panel-content" in detail and "secret-manual-two" not in listing + detail
    assert state(h) == unchanged
    assert set(before_reads["audit_events"]) <= set(unchanged["audit_events"])


DELETE_PAIRS = (
    ("dataset", "dataset.deleted"),
    ("user", "deleted"),
    ("team", "deleted"),
    ("membership", "deleted"),
    ("grant", "deleted"),
    ("dataset_field", "deleted"),
    ("dataset_record", "deleted"),
    ("dataset_export_access", "revoked"),
)


def test_all_catalog_pairs_have_explicit_delete_decisions():
    require_complete_presentation()
    assert set(ACTION_PRESENTATIONS) == set(EVENTS)
    assert {
        pair for pair, metadata in ACTION_PRESENTATIONS.items() if metadata.deletes_object
    } == set(DELETE_PAIRS)
    assert all(type(metadata.deletes_object) is bool for metadata in ACTION_PRESENTATIONS.values())
    assert ("dataset", "deleted") not in EVENTS  # No fictitious Dataset-delete contract.


@pytest.mark.parametrize("pair", DELETE_PAIRS)
def test_successful_delete_event_overrides_name_and_skips_object_resolver(
    history, monkeypatch, pair
):
    h = history
    eid = seed(h, 1, kind=pair[0], action=pair[1], target=h.reader)[0]
    before = state(h)

    def unexpected_lookup(*_args):
        pytest.fail("Committed delete evidence must not depend on current object-name lookup")

    monkeypatch.setattr(SqlAlchemyAuditReadOperations, "resolve_objects", unexpected_lookup)
    # The user ID even resolves to a current name: the delete event still takes precedence.
    listed = h.service.list_history(h.admin).items[0]
    detail = h.service.get_event(h.admin, eid)
    assert listed == detail
    assert (detail.object_status, detail.object_label, detail.object_id) == (
        "deleted",
        "Deleted object",
        h.reader,
    )
    assert state(h) == before


def test_new_contract_requires_explicit_delete_presentation_decision():
    events, actions = dict(EVENTS), dict(ACTION_LABELS)
    pair = ("dataset_record", "purged")
    events[pair] = replace(EVENTS["dataset_record", "deleted"], action="purged")
    actions[pair] = "Record purged"
    with pytest.raises(AuditContractError, match="audit action decisions"):
        require_complete_presentation(events, actions)
    decisions = dict(ACTION_PRESENTATIONS)
    decisions[pair] = ActionPresentation("Record purged", True)
    require_complete_presentation(events, actions, presentations=decisions)
    decisions[pair] = ActionPresentation("Record purged", None)
    with pytest.raises(AuditContractError, match="Explicit audit delete presentation"):
        require_complete_presentation(events, actions, presentations=decisions)


@pytest.mark.parametrize(
    "pair", [("unknown", "deleted"), ("dataset", "deleted"), ("dataset_record", "record.deleted")]
)
def test_unknown_delete_like_name_is_not_deletion_evidence(history, pair):
    eid = seed(history, 1, kind=pair[0], action=pair[1])[0]
    view = history.service.get_event(history.admin, eid)
    assert (view.object_status, view.object_label) == ("unnamed", "No name to display")

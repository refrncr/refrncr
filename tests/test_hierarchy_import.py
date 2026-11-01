"""Editable hierarchy workbook import behavior."""

import io
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from threading import Event

import pytest
from openpyxl import load_workbook
from support import revision

from refrncr.dataset_export import generate_export
from refrncr.hierarchy_contracts import HierarchyNode
from refrncr.record_import import ImportValidationError, TransientImportStore
from refrncr.services import NotFoundError, StaleRecordError


def prepare(c):
    c.d = c.s.datasets.create_dataset(
        c.a.id, "hierarchy", "Hierarchy", "", c.owner.id, kind="hierarchy"
    )
    root = c.s.hierarchy.create_node(c.a.id, c.d.key, "Root", None, expected_revision=revision(c))
    return root


def node(c, name, parent):
    return c.s.hierarchy.create_node(
        c.a.id, c.d.key, name, parent.id, expected_revision=revision(c)
    )


def workbook(fields, nodes):
    artifact = generate_export("xlsx", fields, (), "hierarchy", hierarchy_nodes=nodes)
    content = artifact.content.read()
    artifact.content.close()
    return content


def imported_nodes(*items):
    return tuple(
        HierarchyNode(item.id, "dataset", item.name, item.parent_id, tuple(item.values))
        for item in items
    )


def stored_nodes(c):
    with c.s.hierarchy.uow() as uow:
        return tuple(uow.hierarchy.list_for_dataset(c.d.id))


def exported(c, format_name):
    artifact = c.s.dataset_records.export_dataset(c.a.id, c.d.key, format_name, prefer_concept=True)
    try:
        return artifact.download_name, artifact.content.read()
    finally:
        artifact.content.close()


def changed_workbook(content, field_key, value):
    workbook = load_workbook(io.BytesIO(content))
    try:
        worksheet = workbook["Data"]
        headers = [cell.value for cell in worksheet[1]]
        worksheet.cell(2, headers.index(field_key) + 1, value)
        target = io.BytesIO()
        workbook.save(target)
        return target.getvalue()
    finally:
        workbook.close()


def test_exported_hierarchy_excel_round_trips_and_detects_one_change(database_context):
    c = database_context
    root = prepare(c)
    branch = node(c, "Branch", root)
    leaf = node(c, "Leaf", branch)
    for key, name, data_type in (
        ("valid_on", "Valid on", "date"),
        ("opens_at", "Opens at", "time"),
    ):
        c.s.dataset_fields.create_field(
            c.a.id, c.d.key, key, name, data_type, hierarchy_scope="all_nodes"
        )
    for index, item in enumerate((root, branch, leaf), start=1):
        c.s.hierarchy.update_values(
            c.a.id,
            c.d.key,
            item.id,
            {
                "valid_on": f"2026-0{index}-0{index + 1}",
                "opens_at": f"0{index}:30:00",
            },
            expected_revision=revision(c),
        )

    filename, content = exported(c, "xlsx")
    service = c.s.hierarchy_imports
    state = service.begin(c.a.id, "hierarchy-same", c.d.key, filename, content)
    preview = service.preview(c.a.id, "hierarchy-same", c.d.key, state.token)
    service.cancel(c.a.id, "hierarchy-same", c.d.key, state.token)
    assert preview.valid
    assert (preview.additions, preview.edits, preview.moves, preview.removals) == (0, 0, 0, 0)
    assert preview.unchanged == 3

    state = service.begin(
        c.a.id,
        "hierarchy-changed",
        c.d.key,
        filename,
        changed_workbook(content, "valid_on", "2027-12-31"),
    )
    preview = service.preview(c.a.id, "hierarchy-changed", c.d.key, state.token)
    assert preview.valid
    assert (preview.additions, preview.edits, preview.moves, preview.removals) == (0, 1, 0, 0)
    assert preview.unchanged == 2


def test_hierarchy_csv_is_not_an_editable_import(database_context):
    c = database_context
    root = prepare(c)
    node(c, "Child", root)
    filename, content = exported(c, "csv")

    with pytest.raises(ImportValidationError, match="Upload the editable \\.xlsx hierarchy export"):
        c.s.hierarchy_imports.begin(c.a.id, "hierarchy-csv", c.d.key, filename, content)


def test_hierarchy_import_applies_atomic_difference(database_context):
    c = database_context
    root = prepare(c)
    new_parent = node(c, "New Parent", root)
    branch = node(c, "Branch", root)
    leaf = node(c, "Leaf", branch)
    source = imported_nodes(
        root,
        new_parent,
        HierarchyNode(branch.id, "dataset", "Renamed", new_parent.id),
        leaf,
    )

    service = c.s.hierarchy_imports
    state = service.begin(c.a.id, "session-1", c.d.key, "export.xlsx", workbook((), source))
    preview = service.preview(c.a.id, "session-1", c.d.key, state.token)

    assert preview.valid
    assert preview.additions == 0
    assert preview.edits == 1
    assert preview.moves == 1
    assert preview.removals == 0
    assert next(item for item in preview.rows if item.node_id == branch.id).name == "Renamed"
    assert (
        next(item for item in preview.rows if item.node_id == branch.id).parent_id == new_parent.id
    )
    assert next(item for item in stored_nodes(c) if item.id == branch.id).name == "Branch"

    service.confirm(
        c.a.id,
        "session-1",
        c.d.key,
        state.token,
        confirmation=state.confirmation,
        acknowledge_removals=False,
    )
    saved = next(item for item in stored_nodes(c) if item.id == branch.id)
    assert saved.name == "Renamed"


def test_hierarchy_import_requires_explicit_removal_confirmation(context):
    c = context
    root = prepare(c)
    child = node(c, "Child", root)
    state = c.s.hierarchy_imports.begin(
        c.a.id,
        "session-2",
        c.d.key,
        "export.xlsx",
        workbook((), imported_nodes(root)),
    )
    preview = c.s.hierarchy_imports.preview(c.a.id, "session-2", c.d.key, state.token)
    assert preview.removals == 1

    with pytest.raises(ImportValidationError, match="Explicit confirmation"):
        c.s.hierarchy_imports.confirm(
            c.a.id,
            "session-2",
            c.d.key,
            state.token,
            confirmation=state.confirmation,
            acknowledge_removals=False,
        )
    assert any(item.id == child.id for item in stored_nodes(c))


def test_hierarchy_import_rejects_stale_concept(database_context):
    c = database_context
    root = prepare(c)
    state = c.s.hierarchy_imports.begin(
        c.a.id,
        "session-3",
        c.d.key,
        "export.xlsx",
        workbook((), imported_nodes(root)),
    )
    node(c, "Concurrent", root)

    with pytest.raises(StaleRecordError):
        c.s.hierarchy_imports.confirm(
            c.a.id,
            "session-3",
            c.d.key,
            state.token,
            confirmation=state.confirmation,
            acknowledge_removals=False,
        )


def test_hierarchy_import_cancel_waits_for_busy_operation(database_context):
    c = database_context
    root = prepare(c)
    service = c.s.hierarchy_imports
    service.store = TransientImportStore(cancel_wait_seconds=1)
    state = service.begin(
        c.a.id,
        "session-cancel",
        c.d.key,
        "export.xlsx",
        workbook((), imported_nodes(root)),
    )
    claimed = service.store.claim(state)

    with ThreadPoolExecutor(max_workers=1) as executor:
        cancelling = executor.submit(service.cancel, c.a.id, "session-cancel", c.d.key, state.token)
        with pytest.raises(FuturesTimeoutError):
            cancelling.result(timeout=0.05)
        service.store.finish(claimed)
        cancelling.result(timeout=0.5)

    with pytest.raises(NotFoundError, match="Import not found"):
        service.store.get(state.token, state.actor_id, state.session_id, state.dataset_id)


def test_hierarchy_import_cancel_waits_for_active_preview(database_context, monkeypatch):
    c = database_context
    root = prepare(c)
    service = c.s.hierarchy_imports
    service.store = TransientImportStore(cancel_wait_seconds=1)
    state = service.begin(
        c.a.id,
        "session-preview-cancel",
        c.d.key,
        "export.xlsx",
        workbook((), imported_nodes(root)),
    )
    entered, release = Event(), Event()
    original = service._preview_for

    def held_preview(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=1)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_preview_for", held_preview)
    with ThreadPoolExecutor(max_workers=2) as executor:
        previewing = executor.submit(
            service.preview, c.a.id, "session-preview-cancel", c.d.key, state.token
        )
        assert entered.wait(timeout=1)
        cancelling = executor.submit(
            service.cancel, c.a.id, "session-preview-cancel", c.d.key, state.token
        )
        with pytest.raises(FuturesTimeoutError):
            cancelling.result(timeout=0.05)
        release.set()
        previewing.result(timeout=1)
        cancelling.result(timeout=1)

    with pytest.raises(NotFoundError, match="Import not found"):
        service.store.get(state.token, state.actor_id, state.session_id, state.dataset_id)

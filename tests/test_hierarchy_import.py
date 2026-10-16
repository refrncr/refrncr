"""Editable hierarchy workbook import behavior."""

import pytest
from support import revision

from refrncr.dataset_export import generate_export
from refrncr.hierarchy_contracts import HierarchyNode
from refrncr.record_import import ImportValidationError
from refrncr.services import StaleRecordError


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

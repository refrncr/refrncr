"""Hierarchy relational isolation, retention and complete snapshot restoration."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from refrncr.hierarchy_contracts import HierarchyNode, hierarchy_node_paths
from refrncr.models import (
    DatasetHierarchyNode,
    DatasetHierarchyValue,
    DatasetRecord,
    DatasetVersionHierarchyNode,
    DatasetVersionHierarchyValue,
)
from refrncr.ports import PersistenceConflictError, RecordValue, StoredRecord
from refrncr.version_contracts import SnapshotContent, SnapshotField, SummaryCounts

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


def test_paths_disambiguate_names_and_handle_deep_or_invalid_graphs():
    nodes = (
        HierarchyNode("root", "d", "Root", None),
        HierarchyNode("left", "d", "Left", "root"),
        HierarchyNode("right", "d", "Right", "root"),
        HierarchyNode("one", "d", "Operations", "left"),
        HierarchyNode("two", "d", "Operations", "right"),
    )
    paths = hierarchy_node_paths(reversed(nodes))
    assert paths["one"] == "Root / Left / Operations"
    assert paths["two"] == "Root / Right / Operations"
    deep = tuple(
        HierarchyNode(str(index), "d", "N", str(index - 1) if index else None)
        for index in range(1100)
    )
    assert hierarchy_node_paths(deep)["1099"].count(" / ") == 1099
    invalid = (
        HierarchyNode("one", "d", "One", "two"),
        HierarchyNode("two", "d", "Two", "one"),
        HierarchyNode("missing", "d", "Missing", "absent"),
    )
    paths = hierarchy_node_paths(invalid)
    assert set(paths) == {"one", "two", "missing"}
    assert all(path.startswith("Invalid hierarchy / ") for path in paths.values())


def prepare(c):
    with c.s.datasets.uow(write=True, serialized=True) as work:
        dataset = work.datasets.get(c.d.id, for_update=True)
        dataset.kind = "hierarchy"
        root = work.hierarchy.create(dataset.id, "Root", None)
        child = work.hierarchy.create(dataset.id, "Child", root.id)
    return root, child


def test_nodes_keep_identity_and_values_through_structure_changes(database_context):
    c = database_context
    root, child = prepare(c)
    with c.s.datasets.uow(write=True, serialized=True) as work:
        other = work.hierarchy.create(c.d.id, "Other", root.id)
        grandchild = work.hierarchy.create(c.d.id, "Child", child.id)
        updated = work.hierarchy.update(c.d.id, child.id, "Moved", other.id)
        assert updated.id == child.id and updated.parent_id == other.id
        assert work.hierarchy.get(c.d.id, grandchild.id).parent_id == child.id
        assert work.hierarchy.get(str(uuid4()), child.id) is None
    with c.sessions() as session:
        assert session.scalar(select(func.count()).select_from(DatasetRecord)) == 0


def test_database_rejects_second_root_duplicate_sibling_and_foreign_parent(database_context):
    c = database_context
    root, child = prepare(c)
    foreign = c.s.datasets.create_dataset(c.a.id, "foreign", "Foreign", "", c.owner.id)
    for dataset_id, name, parent_id in (
        (c.d.id, "Second root", None),
        (c.d.id, "Child", root.id),
        (foreign.id, "Foreign child", child.id),
        (c.d.id, "Missing parent", str(uuid4())),
    ):
        with pytest.raises(IntegrityError), c.sessions.begin() as session:
            session.add(DatasetHierarchyNode(dataset_id=dataset_id, name=name, parent_id=parent_id))
    with c.s.datasets.uow() as work:
        assert len(work.hierarchy.list_for_dataset(c.d.id)) == 2


def test_snapshot_restores_inactive_values_scopes_and_ids_without_working_records(database_context):
    c = database_context
    root, child = prepare(c)
    with c.s.datasets.uow(write=True, serialized=True) as work:
        field = work.dataset_fields.create(
            dataset_id=c.d.id,
            key="code",
            name="Code",
            data_type="string",
            is_required=False,
            is_unique=True,
            display_position=1,
            hierarchy_scope="leaves",
        )
        work.flush()
        value = RecordValue(field.id, field.key, "string", "retained", b"sretained", True)
        work.hierarchy.replace_values(c.d.id, root.id, (value,))
        work.hierarchy.replace_values(c.d.id, child.id, (value,))
        assert work.hierarchy.field_has_values(c.d.id, field.id)
        nodes = work.hierarchy.list_for_dataset(c.d.id)
        snapshot = SnapshotContent(
            c.d.name,
            c.d.description,
            (
                SnapshotField(
                    field.id,
                    c.d.id,
                    "code",
                    "Code",
                    "string",
                    False,
                    True,
                    1,
                    False,
                    hierarchy_scope="leaves",
                ),
            ),
            (StoredRecord(child.id, c.d.id, (value,), 1),),
            nodes,
        )
        version = work.versions.append(
            c.d.id,
            1,
            NOW,
            c.a.id,
            NOW.date(),
            "Hierarchy",
            snapshot,
            SummaryCounts(fields_added=1, records_added=1),
        )
    with c.s.datasets.uow(write=True, serialized=True) as work:
        work.hierarchy.replace_values(c.d.id, root.id, ())
        work.hierarchy.update(c.d.id, child.id, "Changed", root.id)
        dataset = work.datasets.get(c.d.id, for_update=True)
        work.concepts.initialize(dataset, version, NOW)
    with c.s.datasets.uow() as work:
        restored = work.hierarchy.list_for_dataset(c.d.id)
        assert {node.id: (node.name, node.parent_id, node.values) for node in restored} == {
            node.id: (node.name, node.parent_id, node.values) for node in nodes
        }
        saved = work.versions.content(version)
        assert len(saved.records) == 1 and saved.records[0].id == child.id
        assert saved.fields[0].hierarchy_scope == "leaves"
        assert len(saved.hierarchy_nodes) == 2
        assert all(node.values == (value,) for node in saved.hierarchy_nodes)
        assert work.dataset_records.count(c.d.id) == 0
    for model in (DatasetVersionHierarchyNode, DatasetVersionHierarchyValue):
        with pytest.raises(PersistenceConflictError), c.sessions.begin() as session:
            row = session.scalars(select(model)).first()
            session.delete(row)


def test_cross_dataset_value_rejected_and_dataset_deletion_clears_tree(database_context):
    c = database_context
    root, _ = prepare(c)
    foreign = c.s.datasets.create_dataset(c.a.id, "foreign", "Foreign", "", c.owner.id)
    with c.s.datasets.uow(write=True, serialized=True) as work:
        field = work.dataset_fields.create(
            dataset_id=foreign.id,
            key="code",
            name="Code",
            data_type="string",
            display_position=1,
        )
        work.flush()
        field_id = field.id
    with pytest.raises(IntegrityError), c.sessions.begin() as session:
        session.add(
            DatasetHierarchyValue(
                dataset_id=c.d.id,
                node_id=root.id,
                field_id=field_id,
                canonical_key=b"sx",
                canonical_text="x",
            )
        )
    with c.s.datasets.uow(write=True, serialized=True) as work:
        assert work.datasets.deletion_counts(c.d.id)[1] == 2
        work.datasets.delete_with_dependents(c.d.id)
    with c.sessions() as session:
        assert session.scalar(select(func.count()).select_from(DatasetHierarchyNode)) == 0

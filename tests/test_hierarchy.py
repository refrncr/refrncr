"""Hierarchy service contracts and atomicity on SQLite and PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy.orm import Session
from test_audit_coverage import audit_db, audits, context, snapshot  # noqa: F401

from refrncr.hierarchy import hierarchy_snapshot, hierarchy_validation_errors
from refrncr.persistence import SqlAlchemyUnitOfWork
from refrncr.services import (
    NotFoundError,
    RecordConflictError,
    RecordValidationError,
    StaleRecordError,
    ValidationError,
)


@pytest.fixture
def hierarchy(context):  # noqa: F811
    c = context
    c.d = c.s.datasets.create_dataset(
        c.a.id, "hierarchy", "Organization", "", c.owner.id, kind="hierarchy"
    )
    return c


def revision(c):
    with c.s.datasets.uow() as work:
        return work.datasets.get(c.d.id).revision


def add(c, name, parent=None):
    return c.s.hierarchy.create_node(c.a.id, c.d.key, name, parent, expected_revision=revision(c))


def move(c, node, parent, name=None):
    return c.s.hierarchy.update_node(
        c.a.id,
        c.d.key,
        node.id,
        name or node.name,
        parent,
        expected_revision=revision(c),
    )


def values(c, node, **submitted):
    return c.s.hierarchy.update_values(
        c.a.id, c.d.key, node.id, submitted, expected_revision=revision(c)
    )


def content(c, validate=False):
    with c.s.datasets.uow() as work:
        return hierarchy_snapshot(work, work.datasets.get(c.d.id), validate=validate)


def errors(c):
    state = content(c)
    with c.s.datasets.uow() as work:
        identifier = work.dataset_business_keys.get(c.d.id)
    return hierarchy_validation_errors(state.fields, identifier, state.hierarchy_nodes)


def field(c, key="code", *, scope="leaves", required=False, unique=False, identifier=False):
    return c.s.dataset_fields.create_field(
        c.a.id,
        c.d.key,
        key,
        key.title(),
        "string",
        required,
        unique,
        identifier,
        hierarchy_scope=scope,
    )


def scope(c, target, value):
    return c.s.dataset_fields.update_field(
        c.a.id,
        c.d.key,
        target.key,
        target.name,
        target.data_type,
        target.is_required,
        target.is_unique,
        hierarchy_scope=value,
    )


def test_hierarchy_first_structure_and_optional_fields(hierarchy):
    c = hierarchy
    root = add(c, "Root")
    assert "at least one child" in " ".join(errors(c))
    with pytest.raises(ValidationError, match="at least one child"):
        content(c, validate=True)
    department = add(c, "Department", root.id)
    leaf = add(c, "Team", department.id)
    short = add(c, "HR", root.id)
    state = content(c, validate=True)
    assert not state.fields and {r.id for r in state.records} == {leaf.id, short.id}
    with c.s.datasets.uow() as work:
        assert work.dataset_records.count(c.d.id) == 0
    assert not errors(c)


def test_parent_constraints_sibling_names_and_stable_subtree(hierarchy):
    c = hierarchy
    root = add(c, "Root")
    sales = add(c, "Sales", root.id)
    engineering = add(c, "Engineering", root.id)
    team = add(c, "Operations", sales.id)
    descendant = add(c, "Support", team.id)
    add(c, "Operations", engineering.id)
    before = snapshot(c)
    for operation in (
        lambda: add(c, "Operations", sales.id),
        lambda: add(c, "Other root"),
        lambda: add(c, "Missing parent", "00000000-0000-0000-0000-000000000001"),
        lambda: move(c, sales, descendant.id),
        lambda: move(c, team, engineering.id),
        lambda: move(c, root, sales.id),
        lambda: move(c, sales, None),
    ):
        with pytest.raises(ValidationError):
            operation()
        assert snapshot(c) == before
    updated = move(c, team, engineering.id, "Customer operations")
    assert updated.id == team.id
    nodes = {node.id: node for node in content(c).hierarchy_nodes}
    assert nodes[descendant.id].parent_id == team.id
    assert nodes[team.id].parent_id == engineering.id


def test_leaf_transition_retains_restores_and_reports_conflicts(hierarchy):
    c = hierarchy
    root = add(c, "Root")
    former_leaf = add(c, "Department", root.id)
    code = field(c, required=True, unique=True)
    values(c, former_leaf, code="A")
    child = add(c, "Team", former_leaf.id)
    assert {r.id for r in content(c).records} == {child.id}
    retained = next(n for n in content(c).hierarchy_nodes if n.id == former_leaf.id)
    assert retained.values[0].value == "A"
    assert "requires a value" in " ".join(errors(c))
    values(c, child, code="A")  # Inactive A on the branch does not occupy uniqueness.
    assert not errors(c)
    move(c, child, root.id)
    assert {r.id for r in content(c).records} == {former_leaf.id, child.id}
    assert "must be unique" in " ".join(errors(c))
    with pytest.raises(ValidationError, match="must be unique"):
        content(c, validate=True)
    values(c, child, code="B")
    assert not errors(c)
    assert code.id == content(c).fields[0].id


def test_field_scope_restoration_and_inactive_value_protection(hierarchy):
    c = hierarchy
    root = add(c, "Root")
    leaf = add(c, "Leaf", root.id)
    code = field(c, scope="all_nodes", unique=True)
    values(c, root, code="A")
    values(c, leaf, code="B")
    scope(c, code, "leaves")
    values(c, leaf, code="A")
    assert not errors(c)
    with pytest.raises(ValidationError, match="Only applicable"):
        values(c, root, code="Forged")
    with pytest.raises(ValidationError, match="stored values"):
        c.s.dataset_fields.update_field(c.a.id, c.d.key, "code", "Code", "integer", False, True)
    with pytest.raises(ValidationError, match="stored values"):
        c.s.dataset_fields.delete_field(c.a.id, c.d.key, "code")
    scope(c, code, "all_nodes")
    assert "must be unique" in " ".join(errors(c))
    with pytest.raises(RecordValidationError):
        values(c, leaf, code="A")  # Ordinary duplicate edits are still rejected.
    values(c, leaf, code="B")
    assert not errors(c)


def test_optional_composite_identifier_and_field_scope_are_independent(hierarchy):
    c = hierarchy
    root = add(c, "Root")
    first = add(c, "First", root.id)
    second = add(c, "Second", root.id)
    field(c, "code", scope="all_nodes", required=True, identifier=True)
    field(c, "region", scope="all_nodes", required=True, identifier=True)
    values(c, root, code="A", region="North")
    values(c, first, code="A", region="North")  # Root is not a record identifier.
    values(c, second, code="A", region="South")
    assert not errors(c)
    with pytest.raises(RecordConflictError, match="another leaf"):
        values(c, second, code="A", region="North")
    field(c, "external", scope="all_nodes", unique=True)
    values(c, root, code="A", region="North", external="ONE")
    with pytest.raises(RecordValidationError):
        values(c, first, code="A", region="North", external="ONE")


@pytest.mark.parametrize("operation", ["create_node", "update_node", "update_values"])
@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_hierarchy_mutations_authorization_stale_cross_dataset_and_atomic_audit(
    hierarchy, monkeypatch, operation, failure
):
    c = hierarchy
    root = add(c, "Root")
    leaf = add(c, "Leaf", root.id)
    field(c)
    before = snapshot(c)
    with pytest.raises(NotFoundError):
        c.s.hierarchy.create_node(c.u.id, c.d.key, "Hidden", expected_revision=revision(c))
    with pytest.raises(StaleRecordError):
        c.s.hierarchy.update_node(c.a.id, c.d.key, leaf.id, "Stale", root.id, expected_revision=1)
    with pytest.raises(NotFoundError):
        c.s.hierarchy.update_values(
            c.a.id,
            c.d.key,
            "00000000-0000-0000-0000-000000000001",
            {},
            expected_revision=revision(c),
        )
    assert snapshot(c) == before

    def fail(*args, **kwargs):
        raise RuntimeError("Injected transaction failure")

    if failure == "audit":
        monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
    else:
        monkeypatch.setattr(Session, "commit", fail)
    with pytest.raises(RuntimeError, match="Injected"):
        if operation == "create_node":
            add(c, "Must roll back", root.id)
        elif operation == "update_node":
            move(c, leaf, root.id, "Must roll back")
        else:
            values(c, leaf, code="Secret must roll back")
    assert snapshot(c) == before


def test_hierarchy_mutations_have_content_free_audit_and_noop(hierarchy):
    c = hierarchy
    root = add(c, "Sensitive root name")
    leaf = add(c, "Sensitive leaf name", root.id)
    field(c)
    values(c, leaf, code="Sensitive value")
    move(c, leaf, root.id, "New sensitive name")
    events = [item for item in audits(c) if item.action.startswith("hierarchy.")]
    assert [item.action for item in events].count("hierarchy.node_created") == 2
    assert {item.action for item in events} == {
        "hierarchy.node_created",
        "hierarchy.node_updated",
        "hierarchy.values_updated",
    }
    assert all(item.object_id == c.d.id and item.details is None for item in events)
    before = snapshot(c)
    values(c, leaf, code="Sensitive value")
    move(c, leaf, root.id, "New sensitive name")
    assert snapshot(c) == before


def test_hierarchy_writers_serialize_and_reject_stale_revision(hierarchy):
    c = hierarchy
    root = add(c, "Root")
    expected = revision(c)
    barrier = Barrier(2)

    def create(name):
        barrier.wait(timeout=10)
        try:
            return c.s.hierarchy.create_node(
                c.a.id, c.d.key, name, root.id, expected_revision=expected
            )
        except StaleRecordError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, ("First", "Second")))
    assert sum(item is not None for item in results) == 1
    assert len(content(c).hierarchy_nodes) == 2


def test_generic_record_import_and_export_paths_reject_hierarchies(hierarchy):
    c = hierarchy
    root = add(c, "Root")
    add(c, "Leaf", root.id)
    before = snapshot(c)
    with pytest.raises(ValidationError, match="hierarchy views"):
        c.s.dataset_records.create_record(c.a.id, c.d.key, {})
    with pytest.raises(ValidationError, match="hierarchy views"):
        c.s.record_imports.eligibility(c.a.id, c.d.key)
    with pytest.raises(ValidationError, match="hierarchy views"):
        c.s.dataset_records.export_dataset(c.a.id, c.d.key, "csv", prefer_concept=True)
    assert snapshot(c) == before

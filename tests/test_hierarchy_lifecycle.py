"""Complete hierarchy versions, selected reads, review and concept restoration."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import select, update
from test_audit_coverage import audit_db, audits, context, snapshot  # noqa: F401
from test_dataset_versions import NOW, content, publish, revision

from refrncr.dataset_diff import DatasetDiffService
from refrncr.dataset_versions import DatasetVersionService
from refrncr.models import DatasetRecord, DatasetVersionRecord
from refrncr.services import ConflictError, NotFoundError, ValidationError


def prepare(c):
    c.d = c.s.datasets.create_dataset(
        c.a.id, "hierarchy", "Hierarchy", "", c.owner.id, kind="hierarchy"
    )
    c.service = DatasetVersionService(
        c.s.datasets.uow, c.s.authorization.datasets, clock=lambda: NOW
    )
    c.diff = DatasetDiffService(c.service)
    return node(c, "Root")


def node(c, name, parent=None):
    return c.s.hierarchy.create_node(c.a.id, c.d.key, name, parent, expected_revision=revision(c))


def move(c, item, name=None, parent=None):
    return c.s.hierarchy.update_node(
        c.a.id,
        c.d.key,
        item.id,
        name or item.name,
        parent,
        expected_revision=revision(c),
    )


def values(c, item, submitted):
    return c.s.hierarchy.update_values(
        c.a.id, c.d.key, item.id, submitted, expected_revision=revision(c)
    )


def fields(c, *, scope="leaves", unique=False):
    return c.s.dataset_fields.create_field(
        c.a.id,
        c.d.key,
        "code",
        "Code",
        "string",
        False,
        unique,
        hierarchy_scope=scope,
    )


def test_empty_fields_complete_snapshot_and_selected_leaf_reads(context):  # noqa: F811
    c = context
    root = prepare(c)
    branch = node(c, "Branch", root.id)
    leaf = node(c, "Leaf", branch.id)
    other = node(c, "Other", root.id)
    review = c.diff.open_review(c.a.id, c.d.id)
    assert review.counts.records_added == 2
    assert {change.id for change in review.hierarchy_changes} == {
        root.id,
        branch.id,
        leaf.id,
        other.id,
    }
    first = publish(c)
    saved = content(c, first)
    assert saved.fields == () and saved.identifier_keys == ()
    assert {record.id for record in saved.records} == {leaf.id, other.id}
    assert {item.id for item in saved.hierarchy_nodes} == {root.id, branch.id, leaf.id, other.id}
    selected = c.service.read(c.a.id, c.d.key)
    assert selected.state.version.id == first.id
    assert selected.context.record_count == selected.page.total == 2
    assert selected.hierarchy_nodes == saved.hierarchy_nodes
    assert selected.hierarchy_errors == ()
    assert selected.record_labels[leaf.id] == "Root / Branch / Leaf"
    assert not selected.may_export
    with c.sessions() as session:
        assert not session.scalars(
            select(DatasetRecord).where(DatasetRecord.dataset_id == c.d.id)
        ).all()
    c.service.create_concept(c.a.id, c.d.id)
    with pytest.raises(ConflictError, match="matches"):
        publish(c)
    with pytest.raises(NotFoundError):
        c.service.read_current(c.a.id, c.d.key)
    assert all(item.key != c.d.key for item in c.service.read_current(c.a.id).publications)
    for selection in ({}, {"version_number": 1}, {"effective_date": NOW.date()}):
        assert c.service.read_current(c.a.id, **selection).total == 0
        with pytest.raises(NotFoundError):
            c.service.read_current(c.a.id, c.d.key, **selection)


def test_structure_only_review_publication_and_old_snapshot_are_independent(context):  # noqa: F811
    c = context
    root = prepare(c)
    branch = node(c, "Branch", root.id)
    leaf = node(c, "Leaf", branch.id)
    first = publish(c)
    saved = content(c, first)
    c.service.create_concept(c.a.id, c.d.id)
    move(c, branch, "Renamed", root.id)
    review = c.diff.open_review(c.a.id, c.d.id)
    assert review.has_changes and review.counts.records_changed == 0
    assert any(change.id == branch.id for change in review.hierarchy_changes)
    assert c.service.concept_context(c.a.id, c.d.key).may_publish
    second = publish(c)
    assert content(c, first) == saved
    assert {item.id for item in content(c, second).hierarchy_nodes} == {root.id, branch.id, leaf.id}
    historic = c.service.read(c.a.id, c.d.key, first.id)
    assert next(item.name for item in historic.hierarchy_nodes if item.id == branch.id) == "Branch"
    assert (
        next(
            item.name
            for item in c.service.read(c.a.id, c.d.key).hierarchy_nodes
            if item.id == branch.id
        )
        == "Renamed"
    )


def test_retained_values_survive_publication_and_conflicts_remain_reviewable(context):  # noqa: F811
    c = context
    root = prepare(c)
    former_leaf = node(c, "Former leaf", root.id)
    other = node(c, "Other", root.id)
    fields(c, unique=True)
    values(c, former_leaf, {"code": "shared"})
    values(c, other, {"code": "different"})
    child = node(c, "Child", former_leaf.id)
    values(c, other, {"code": "shared"})
    first = publish(c)
    saved = content(c, first)
    assert (
        next(n for n in saved.hierarchy_nodes if n.id == former_leaf.id).values[0].value == "shared"
    )
    c.service.create_concept(c.a.id, c.d.id)
    move(c, child, parent=root.id)
    selected = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    assert former_leaf.id in {record.id for record in selected.page.records}
    assert any("must be unique" in message for message in selected.hierarchy_errors)
    review = c.diff.open_review(c.a.id, c.d.id)
    assert review.hierarchy_errors
    assert not c.service.concept_context(c.a.id, c.d.key).may_publish
    before = snapshot(c)
    with pytest.raises(ValidationError, match="must be unique"):
        publish(c)
    assert snapshot(c) == before
    values(c, former_leaf, {"code": "resolved"})
    second = publish(c)
    assert content(c, first) == saved
    assert {r.id for r in content(c, second).records} == {former_leaf.id, other.id, child.id}


def test_branch_values_and_scope_changes_are_versioned_and_reviewed(context):  # noqa: F811
    c = context
    root = prepare(c)
    leaf = node(c, "Leaf", root.id)
    fields(c, scope="all_nodes")
    values(c, root, {"code": "root-value"})
    values(c, leaf, {"code": "leaf-value"})
    first = publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    values(c, root, {"code": "updated-root"})
    review = c.diff.open_review(c.a.id, c.d.id)
    assert review.counts.records_changed == 0
    assert any(change.id == root.id for change in review.hierarchy_changes)
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "code", "Code", "string", False, False, hierarchy_scope="leaves"
    )
    second = publish(c)
    assert content(c, second).fields[0].hierarchy_scope == "leaves"
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "code", "Code", "string", False, False, hierarchy_scope="all_nodes"
    )
    selected = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    assert (
        next(n for n in selected.hierarchy_nodes if n.id == root.id).values[0].value
        == "updated-root"
    )
    assert (
        next(n for n in content(c, first).hierarchy_nodes if n.id == root.id).values[0].value
        == "root-value"
    )


def test_discard_and_cancel_restore_complete_baseline(context):  # noqa: F811
    c = context
    root = prepare(c)
    branch = node(c, "Branch", root.id)
    node(c, "Leaf", branch.id)
    fields(c, scope="all_nodes")
    values(c, branch, {"code": "saved"})
    first = publish(c)
    original = content(c, first)
    c.service.create_concept(c.a.id, c.d.id)
    move(c, branch, "Working name", root.id)
    values(c, branch, {"code": "working"})
    discard = c.service.review_discard(c.a.id, c.d.id)
    c.service.discard_concept(c.a.id, c.d.id, discard.identity)
    assert c.service.read(c.a.id, c.d.key).hierarchy_nodes == original.hierarchy_nodes
    c.service.create_concept(c.a.id, c.d.id)
    working = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    assert [(n.id, n.name, n.parent_id, n.values) for n in working.hierarchy_nodes] == [
        (n.id, n.name, n.parent_id, n.values) for n in original.hierarchy_nodes
    ]
    move(c, branch, "Scheduled", root.id)
    second = publish(c, day=NOW.date() + timedelta(days=1))
    c.service.cancel(c.a.id, c.d.id, second.id)
    c.service.create_concept(c.a.id, c.d.id)
    restored = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    assert next(n for n in restored.hierarchy_nodes if n.id == branch.id).name == "Branch"
    assert not c.service.concept_context(c.a.id, c.d.key).has_changes


def test_hierarchy_review_authority_staleness_and_atomic_publication(context, monkeypatch):  # noqa: F811
    c = context
    root = prepare(c)
    leaf = node(c, "Leaf", root.id)
    with pytest.raises(NotFoundError):
        c.diff.open_review(c.u.id, c.d.id)
    review = c.diff.open_review(c.a.id, c.d.id)
    move(c, leaf, "Renamed", root.id)
    with pytest.raises(ConflictError, match="changed"):
        c.diff.review(c.a.id, review.identity)
    from refrncr.persistence import SqlAlchemyUnitOfWork

    original = SqlAlchemyUnitOfWork.add_audit

    def fail(work, **entry):
        if entry["action"] == "dataset.version_published":
            raise RuntimeError("Injected audit failure")
        original(work, **entry)

    before = snapshot(c)
    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail)
    with pytest.raises(RuntimeError, match="Injected"):
        publish(c)
    assert snapshot(c) == before
    assert not any(event.action == "dataset.version_published" for event in audits(c))


def test_optional_identifier_after_tree_and_all_nodes_components(context):  # noqa: F811
    c = context
    root = prepare(c)
    branch = node(c, "Branch", root.id)
    leaf = node(c, "Leaf", branch.id)
    c.s.dataset_fields.create_field(
        c.a.id, c.d.key, "code", "Code", "string", True, False, hierarchy_scope="all_nodes"
    )
    for item in (root, branch, leaf):
        values(c, item, {"code": "same"})
    c.s.dataset_business_keys.set_business_key(c.a.id, c.d.key, ("code",))
    first = publish(c)
    saved = content(c, first)
    assert saved.identifier_keys == ("code",)
    assert first.record_count == 1
    c.service.create_concept(c.a.id, c.d.id)
    with c.s.datasets.uow() as work:
        assert work.version_diff.validate_snapshot(first)
    move(c, leaf, "Renamed", branch.id)
    review = c.diff.open_review(c.a.id, c.d.id)
    detail = c.diff.record_detail(c.a.id, review.identity, leaf.id)
    assert detail.record.before_identifier == "Root / Branch / Leaf"
    assert detail.record.after_identifier == "Root / Branch / Renamed"
    assert c.service.read(c.a.id, c.d.key, prefer_concept=True).record_labels[leaf.id] == "same"
    publish(c)


def test_corrupt_leaf_snapshot_identifier_fails_closed(context):  # noqa: F811
    c = context
    root = prepare(c)
    node(c, "Leaf", root.id)
    first = publish(c)
    with c.sessions.begin() as session:
        session.execute(
            update(DatasetVersionRecord)
            .where(DatasetVersionRecord.version_id == first.id)
            .values(identifier_key=b"corrupt")
        )
    with pytest.raises(ValidationError, match="Invalid published snapshot"):
        c.service.create_concept(c.a.id, c.d.id)


def test_hierarchy_mutation_and_publication_share_one_revision_lock(context):  # noqa: F811
    c = context
    root = prepare(c)
    leaf = node(c, "Leaf", root.id)
    expected = revision(c)
    ready = Barrier(2)

    def run(operation):
        ready.wait(timeout=10)
        try:
            if operation == "publish":
                c.service.publish(c.a.id, c.d.id, NOW.date(), "", expected)
            else:
                c.s.hierarchy.update_node(
                    c.a.id, c.d.key, leaf.id, "Renamed", root.id, expected_revision=expected
                )
            return True
        except ConflictError:
            return False

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(run, ("publish", "mutate")))
    assert sum(results) == 1
    selected = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    selected_leaf = next(item for item in selected.hierarchy_nodes if item.id == leaf.id)
    if results[0]:
        assert selected.state is not None and selected_leaf.name == "Leaf"
    else:
        assert selected.state is None and selected_leaf.name == "Renamed"

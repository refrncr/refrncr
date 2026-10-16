"""Hierarchy presentation counts include every node without changing snapshot records."""

import pytest
from support import content, node, prepare, publish, snapshot

from refrncr.version_persistence import SqlAlchemyVersionOperations


def test_root_only_concept_counts_the_root(context):
    c = context
    root = prepare(c)
    before = snapshot(c)
    selected = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    assert selected.context.record_count == 1
    assert selected.page.records == () and selected.page.total == 0
    assert [item.id for item in selected.hierarchy_nodes] == [root.id]
    assert c.service.context(c.a.id, c.d.key).record_count == 1
    with c.s.datasets.uow() as work:
        scoped = work.datasets.current_overview((c.d.id,))
        assert [(item.dataset.id, item.record_count) for item in scoped] == [(c.d.id, 1)]
        assert (
            next(
                item.record_count
                for item in work.datasets.current_overview(None)
                if item.dataset.id == c.d.id
            )
            == 1
        )
        assert work.datasets.current_overview(()) == []
        assert work.datasets.deletion_counts(c.d.id)[1] == 1
    assert snapshot(c) == before


def test_concept_current_and_historical_counts_use_their_own_nodes(context, monkeypatch):
    c = context
    root = prepare(c)
    branch = node(c, "Branch", root.id)
    leaf = node(c, "Leaf", branch.id)
    node(c, "Other", root.id)
    first = publish(c)
    original = content(c, first)
    c.service.create_concept(c.a.id, c.d.id)
    node(c, "Deeper leaf", leaf.id)
    assert c.service.context(c.a.id, c.d.key).record_count == 5
    assert c.service.read(c.a.id, c.d.key).context.record_count == 4
    second = publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    node(c, "Private concept addition", root.id)
    before = snapshot(c)

    concept = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    current = c.service.read(c.a.id, c.d.key)
    historic = c.service.read(c.a.id, c.d.key, first.id)
    assert concept.context.record_count == len(concept.hierarchy_nodes) == 6
    assert current.context.record_count == len(current.hierarchy_nodes) == 5
    assert historic.context.record_count == len(historic.hierarchy_nodes) == 4
    assert (
        current.page.total == historic.page.total == first.record_count == second.record_count == 2
    )
    assert {item.id for item in historic.page.records} == {item.id for item in original.records}
    assert content(c, first) == original

    with monkeypatch.context() as patch:
        patch.setattr(
            SqlAlchemyVersionOperations,
            "hierarchy_nodes",
            lambda *_args: pytest.fail("Overview counts must not load hierarchy node values"),
        )
        for actor in (c.a, c.u):
            overview = next(
                item for item in c.service.overview(actor.id) if item.dataset.id == c.d.id
            )
            assert overview.record_count == 5
    with c.s.datasets.uow() as work:
        assert work.datasets.deletion_counts(c.d.id)[1] == 6
        assert work.versions.hierarchy_node_counts((first.id, second.id)) == {
            first.id: 4,
            second.id: 5,
        }
        assert work.versions.hierarchy_node_counts(()) == {}
        assert work.versions.get(c.d.id, first.id).record_count == 2
        assert work.versions.get(c.d.id, second.id).record_count == 2
    assert snapshot(c) == before

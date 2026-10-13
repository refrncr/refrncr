"""Tree/Table hierarchy presentation and node-panel regressions."""

import re
from html import unescape

import pytest
from dataset_helpers import create_owned_dataset
from test_dataset_browser import body, login, request, session_csrf
from test_dataset_browser import dataset_browser as dataset_browser
from test_hierarchy_browser import hierarchy_fixture, panel_revision


def set_values(services, admin, dataset, hierarchy, node, values):
    hierarchy.update_values(
        admin.id,
        dataset.key,
        node.id,
        values,
        expected_revision=services.datasets.get_dataset(admin.id, dataset.key).revision,
    )


def populated_hierarchy(services, admin):
    dataset, hierarchy, root, branch, child, sibling = hierarchy_fixture(services, admin)
    for key, name, data_type, scope in (
        ("code", "Code", "string", "leaves"),
        ("owner", "Owner", "string", "all_nodes"),
        ("count", "Count", "integer", "all_nodes"),
    ):
        services.dataset_fields.create_field(
            admin.id, dataset.key, key, name, data_type, hierarchy_scope=scope
        )
    for node, values in (
        (root, {"owner": "Root owner", "count": "11"}),
        (branch, {"owner": "Branch owner", "count": "22"}),
        (child, {"code": "C", "owner": "Leaf owner", "count": "33"}),
        (sibling, {"code": "H"}),
    ):
        set_values(services, admin, dataset, hierarchy, node, values)
    return dataset, hierarchy, root, branch, child, sibling


def table_markup(page):
    match = re.search(r"<table class='hierarchy-table[^']*'[^>]*>(.*?)</table>", page, re.S)
    assert match is not None
    return match.group(1)


def visible_text(markup):
    markup = re.sub(r"<[^>]+>", "", markup)
    return " ".join(unescape(markup).split())


def row_order(page):
    return re.findall(r"<tr data-hierarchy-node='([^']+)'", page)


def test_table_is_one_node_per_row_with_name_parent_level_and_values(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, _hierarchy, root, branch, child, sibling = populated_hierarchy(services, admin)
    login(client)

    page = body(request(client, "get", f"/datasets/{dataset.key}/concept/records?view=table"))
    table = table_markup(page)

    assert all(
        f"data-hierarchy-node='{node.id}'" in page for node in (root, branch, child, sibling)
    )
    assert all(label in table for label in ("Name", "Parent", "Level", "Code", "Owner", "Count"))
    assert "records_view" not in page
    assert "Marketing" in table and "Executive" in table
    assert "Leaves only" in table and "aria-label='Not applicable'" in table


def test_tree_contains_values_and_only_structure_actions(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, _hierarchy, root, branch, child, _sibling = populated_hierarchy(services, admin)
    login(client)

    page = body(request(client, "get", f"/datasets/{dataset.key}/concept/records"))
    assert "class='hierarchy-table hierarchy-tree'" in page
    assert "Branch owner" in page
    assert f"aria-label='Edit node {branch.name}'" in page
    assert f"aria-label='Add child to {branch.name}'" in page
    assert f"aria-label='Delete branch {branch.name}'" in page
    assert f"aria-label='Delete branch {root.name}'" not in page
    assert "Edit values for" not in page
    assert "Actions" in page


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("view=hierarchy", "hierarchy-tree"),
        ("view=hybrid", "hierarchy-tree"),
        ("view=records", "hierarchy-table"),
    ],
)
def test_legacy_views_are_accepted_as_tree_or_table_aliases(dataset_browser, query, expected):
    _, services, admin, client = dataset_browser
    dataset, *_ = populated_hierarchy(services, admin)
    login(client)
    response = request(client, "get", f"/datasets/{dataset.key}/concept/records?{query}")
    assert response.status_code == 200
    assert expected in body(response)


def test_table_sorting_changes_display_order_without_mutating_nodes(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, _hierarchy, root, branch, child, sibling = populated_hierarchy(services, admin)
    login(client)
    before = [node.id for node in (root, branch, child, sibling)]

    response = request(
        client,
        "get",
        f"/datasets/{dataset.key}/concept/records?view=table&sort=name&direction=asc",
    )
    assert response.status_code == 200
    order = row_order(body(response))
    assert order.index(root.id) < order.index(branch.id)
    assert before == [node.id for node in (root, branch, child, sibling)]
    assert "sort=parent" in body(response)


def test_node_panel_contains_structure_and_values_in_one_form(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, _hierarchy, _root, branch, _child, _sibling = populated_hierarchy(services, admin)
    login(client)
    panel = body(request(client, "get", f"/datasets/{dataset.key}/concept/nodes/{branch.id}/edit"))
    assert "Edit node" in panel
    assert "name='name'" in panel
    assert "name='parent_id'" in panel
    assert "name='field:owner'" in panel
    assert "Save node" in panel


def test_editing_node_and_value_uses_one_revision_and_transaction(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, hierarchy, _root, branch, _child, _sibling = populated_hierarchy(services, admin)
    login(client)
    panel = request(client, "get", f"/datasets/{dataset.key}/concept/nodes/{branch.id}/edit")
    response = request(
        client,
        "post",
        f"/datasets/{dataset.key}/concept/nodes/{branch.id}/edit",
        data={
            "csrf_token": session_csrf(client, services),
            "expected_revision": panel_revision(panel),
            "name": "Marketing updated",
            "parent_id": branch.parent_id,
            "field:owner": "Updated owner",
            "field:count": "42",
        },
    )
    assert response.status_code == 302
    saved = body(request(client, "get", response.location))
    assert "Marketing updated" in saved
    assert "Updated owner" in saved


def test_empty_hierarchy_still_offers_add_root(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_owned_dataset(
        services.datasets, admin.id, "empty", "Empty", "", kind="hierarchy"
    )
    login(client)
    page = body(request(client, "get", f"/datasets/{dataset.key}/concept/records"))
    assert "Start with the root" in page
    assert "Add root" in page

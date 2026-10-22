"""Tree/Table hierarchy presentation and node-panel regressions."""

import re
from datetime import UTC, datetime
from html import unescape

import pytest
from dataset_helpers import create_owned_dataset
from support import body, hierarchy_fixture, login, panel_revision, request, session_csrf
from werkzeug.datastructures import MultiDict


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
    ("query", "expected", "has_records_view"),
    [
        ("view=hierarchy&q=Marketing", "hierarchy-tree", False),
        ("view=hybrid&q=Marketing&records_view=flattened", "hierarchy-tree", True),
        ("view=records&q=Marketing", "hierarchy-table", False),
        ("view=records&q=Marketing&records_view=reference", "hierarchy-table", True),
    ],
)
def test_legacy_views_redirect_to_canonical_tabs(
    dataset_browser, query, expected, has_records_view
):
    _, services, admin, client = dataset_browser
    dataset, *_ = populated_hierarchy(services, admin)
    login(client)
    response = request(client, "get", f"/datasets/{dataset.key}/concept/records?{query}")
    assert response.status_code == 301
    assert f"view={'table' if expected.endswith('table') else 'tree'}" in response.location
    assert "q=Marketing" in response.location
    assert ("records_view=" in response.location) is False


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


def test_table_key_sort_orders_numeric_values_numerically(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, hierarchy, root, branch, child, sibling = populated_hierarchy(services, admin)
    services.dataset_fields.create_field(
        admin.id,
        dataset.key,
        "sort_number",
        "Sort number",
        "number",
        is_required=True,
        hierarchy_scope="all_nodes",
    )
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("sort_number",))
    for node, number in ((root, "1"), (branch, "10"), (child, "2.5"), (sibling, "2")):
        set_values(services, admin, dataset, hierarchy, node, {"sort_number": number})
    login(client)
    response = request(
        client,
        "get",
        f"/datasets/{dataset.key}/concept/records?view=table&sort=key&direction=asc",
    )
    assert response.status_code == 200
    order = row_order(body(response))
    assert order.index(sibling.id) < order.index(child.id) < order.index(branch.id)


def test_combined_panel_rejects_leaves_only_field_on_branch(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, hierarchy, _root, branch, _child, _sibling = populated_hierarchy(services, admin)
    login(client)
    url = f"/datasets/{dataset.key}/concept/nodes/{branch.id}/edit"
    panel = request(client, "get", url)
    response = request(
        client,
        "post",
        url,
        data={
            "csrf_token": session_csrf(client, services),
            "expected_revision": panel_revision(panel),
            "name": "Should not save",
            "parent_id": branch.parent_id,
            "field:code": "invalid-on-branch",
            "field:owner": "Branch owner",
            "field:count": "22",
        },
    )
    assert response.status_code == 400
    current = body(request(client, "get", url))
    assert "Should not save" not in current
    assert "Marketing" in current
    assert "invalid-on-branch" not in current


def test_combined_panel_rejects_unique_value_without_changes(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, hierarchy, _root, branch, child, _sibling = populated_hierarchy(services, admin)
    services.dataset_fields.create_field(
        admin.id,
        dataset.key,
        "unique_owner",
        "Unique owner",
        "string",
        is_unique=True,
        hierarchy_scope="all_nodes",
    )
    set_values(services, admin, dataset, hierarchy, child, {"unique_owner": "taken"})
    login(client)
    url = f"/datasets/{dataset.key}/concept/nodes/{branch.id}/edit"
    panel = request(client, "get", url)
    response = request(
        client,
        "post",
        url,
        data={
            "csrf_token": session_csrf(client, services),
            "expected_revision": panel_revision(panel),
            "name": "Should not save",
            "parent_id": branch.parent_id,
            "field:owner": "Branch owner",
            "field:count": "22",
            "field:unique_owner": "taken",
        },
    )
    assert response.status_code == 400
    current = body(request(client, "get", url))
    assert "Should not save" not in current
    assert "Marketing" in current
    assert "value='taken'" not in current


def test_save_from_table_preserves_search_and_sort_context(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, _hierarchy, _root, branch, _child, _sibling = populated_hierarchy(services, admin)
    login(client)
    url = (
        f"/datasets/{dataset.key}/concept/nodes/{branch.id}/edit"
        "?view=table&q=Marketing&sort=name&direction=desc"
    )
    panel = request(client, "get", url)
    response = request(
        client,
        "post",
        f"/datasets/{dataset.key}/concept/nodes/{branch.id}/edit",
        data={
            "csrf_token": session_csrf(client, services),
            "expected_revision": panel_revision(panel),
            "return_view": "table",
            "return_q": "Marketing",
            "return_sort": "name",
            "return_direction": "desc",
            "name": "Marketing saved",
            "parent_id": branch.parent_id,
            "field:owner": "Branch owner",
            "field:count": "22",
        },
    )
    assert response.status_code == 302
    assert response.location.endswith("?view=table&q=Marketing&sort=name&direction=desc")


def test_cancel_link_preserves_view_and_search_without_empty_direction(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, _hierarchy, _root, branch, _child, _sibling = populated_hierarchy(services, admin)
    login(client)
    page = body(
        request(
            client,
            "get",
            f"/datasets/{dataset.key}/concept/nodes/{branch.id}/edit?view=table&q=Marketing",
        )
    )
    cancel = re.search(r"<a class='button button-secondary' data-panel-cancel href='([^']+)'", page)
    assert cancel is not None
    assert "view=table" in cancel.group(1)
    assert "q=Marketing" in cancel.group(1)
    assert "direction=" not in cancel.group(1)


def test_columns_default_includes_key_and_six_fields(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, _hierarchy, *_nodes = hierarchy_fixture(services, admin)
    for index in range(7):
        services.dataset_fields.create_field(
            admin.id,
            dataset.key,
            f"field_{index}",
            f"Field {index}",
            "string",
            is_required=index == 0,
            hierarchy_scope="all_nodes",
        )
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("field_0",))
    login(client)
    page = body(request(client, "get", f"/datasets/{dataset.key}/concept/records?view=table"))
    inputs = re.findall(r"<input type='checkbox'[^>]*data-column-toggle[^>]*>", page)
    assert sum(" checked" in item for item in inputs) == 6
    assert any("value='field_0'" in item and " disabled" in item for item in inputs)
    assert "data-column-count>6</span> of 7" in page


def test_published_snapshot_panels_have_no_actions_and_keep_old_name(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, hierarchy, _root, branch, _child, _sibling = hierarchy_fixture(services, admin)
    published = services.dataset_versions.publish(
        admin.id,
        dataset.id,
        datetime.now(UTC).date(),
        "Structure",
        services.datasets.get_dataset(admin.id, dataset.key).revision,
    )
    services.dataset_versions.create_concept(admin.id, dataset.id)
    hierarchy.update_node(
        admin.id,
        dataset.key,
        branch.id,
        "Renamed branch",
        branch.parent_id,
        expected_revision=services.datasets.get_dataset(admin.id, dataset.key).revision,
    )
    login(client)
    page = body(
        request(
            client,
            "get",
            f"/datasets/{dataset.key}/versions/{published.id}/records?view=table",
        )
    )
    assert "<th scope='col' class='hierarchy-actions-column'>Actions</th>" not in page
    assert "Renamed branch" not in page
    assert "Marketing" in page
    assert f"data-dialog-open='hierarchy-node-{branch.id}'" in page
    assert "Read only" in page


def test_node_panel_contains_structure_and_values_in_one_form(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, _hierarchy, _root, branch, _child, _sibling = populated_hierarchy(services, admin)
    login(client)
    panel = body(request(client, "get", f"/datasets/{dataset.key}/concept/nodes/{branch.id}/edit"))
    assert "Edit node" in panel
    assert "name='node_label'" in panel
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


def test_combined_node_panel_rejects_stale_revision_without_partial_changes(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, hierarchy, _root, branch, _child, _sibling = populated_hierarchy(services, admin)
    login(client)
    url = f"/datasets/{dataset.key}/concept/nodes/{branch.id}/edit"
    panel = request(client, "get", url)
    stale_revision = panel_revision(panel)
    hierarchy.update_node(
        admin.id,
        dataset.key,
        branch.id,
        "Concurrent update",
        branch.parent_id,
        expected_revision=services.datasets.get_dataset(admin.id, dataset.key).revision,
    )
    response = request(
        client,
        "post",
        url,
        data={
            "csrf_token": session_csrf(client, services),
            "expected_revision": stale_revision,
            "name": "Should not save",
            "parent_id": branch.parent_id,
            "field:owner": "Should not save",
            "field:count": "99",
        },
    )
    assert response.status_code == 409
    current = body(request(client, "get", url))
    assert "Should not save" not in current
    assert "Concurrent update" in current
    assert "Branch owner" in current


def test_combined_node_panel_rerenders_submitted_values_on_validation_error(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, _hierarchy, root, branch, _child, _sibling = populated_hierarchy(services, admin)
    login(client)
    url = f"/datasets/{dataset.key}/concept/nodes/{branch.id}/edit"
    panel = request(client, "get", url)
    response = request(
        client,
        "post",
        url,
        data={
            "csrf_token": session_csrf(client, services),
            "expected_revision": panel_revision(panel),
            "name": "Submitted name",
            "parent_id": root.id,
            "field:owner": "Submitted owner",
            "field:count": "not-a-number",
        },
    )
    page = body(response)
    assert response.status_code == 400
    assert "Submitted name" in page
    assert "Submitted owner" in page
    assert "not-a-number" in page


def test_combined_node_panel_rejects_duplicate_field_input(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, _hierarchy, _root, _branch, child, _sibling = populated_hierarchy(services, admin)
    login(client)
    url = f"/datasets/{dataset.key}/concept/nodes/{child.id}/edit"
    panel = request(client, "get", url)
    response = request(
        client,
        "post",
        url,
        data=MultiDict(
            [
                ("csrf_token", session_csrf(client, services)),
                ("expected_revision", panel_revision(panel)),
                ("name", child.name),
                ("parent_id", child.parent_id),
                ("field:code", "first"),
                ("field:code", "second"),
                ("field:owner", "owner"),
                ("field:count", "1"),
            ]
        ),
    )
    assert response.status_code == 400


def test_empty_hierarchy_still_offers_add_root(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_owned_dataset(
        services.datasets, admin.id, "empty", "Empty", "", kind="hierarchy"
    )
    login(client)
    page = body(request(client, "get", f"/datasets/{dataset.key}/concept/records"))
    assert "Start with the root" in page
    assert "Add root" in page

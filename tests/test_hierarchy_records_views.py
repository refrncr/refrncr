"""Hierarchy Records representations, selected snapshots and value-edit navigation."""

import re
from datetime import UTC, datetime
from html import unescape
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

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
        ("legacy", "Legacy", "string", "all_nodes"),
    ):
        services.dataset_fields.create_field(
            admin.id, dataset.key, key, name, data_type, hierarchy_scope=scope
        )
    for node, values in (
        (root, {"owner": "Root owner", "count": "11"}),
        (branch, {"owner": "Branch owner", "count": "22", "legacy": "Inactive secret"}),
        (child, {"code": "C", "owner": "Leaf owner", "count": "33"}),
        (sibling, {"code": "H"}),
    ):
        set_values(services, admin, dataset, hierarchy, node, values)
    services.dataset_fields.update_field(
        admin.id, dataset.key, "legacy", "Legacy", "string", False, False, hierarchy_scope="leaves"
    )
    return SimpleNamespace(
        dataset=dataset, hierarchy=hierarchy, root=root, branch=branch, child=child, sibling=sibling
    )


def table_markup(page):
    match = re.search(r"<table class='hierarchy-table[^']*'>(.*?)</table>", page, re.S)
    assert match is not None
    return match.group(1)


def visible_text(markup):
    markup = re.sub(r"<noscript>.*?</noscript>", "", markup, flags=re.S)
    markup = re.sub(r"<span class='visually-hidden'>.*?</span>", "", markup, flags=re.S)
    return " ".join(unescape(re.sub(r"<[^>]+>", "", markup)).split())


def headers(table):
    heading = re.search(r"<thead>(.*?)</thead>", table, re.S).group(1)
    return [visible_text(cell) for cell in re.findall(r"<th\b[^>]*>(.*?)</th>", heading, re.S)]


def row_cells(table, node):
    row = re.search(rf"<tr data-hierarchy-node='{node.id}'>(.*?)</tr>", table, re.S)
    assert row is not None
    return re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row.group(1), re.S)


def records_selector(page):
    selector = re.search(
        r"<nav class='section-tabs' aria-label='Records views'>(.*?)</nav>", page, re.S
    )
    assert selector is not None
    return [unescape(href) for href in re.findall(r"href='([^']+)'", selector.group(1))]


def assert_return_view(panel, dataset, records_view):
    assert f"name='return_records_view' value='{records_view}'" in panel
    cancel = re.search(r"data-panel-cancel href='([^']+)'", panel)
    assert cancel is not None
    url = urlsplit(unescape(cancel.group(1)))
    assert url.path == f"/datasets/{dataset.key}/concept/records"
    assert parse_qs(url.query) == {"view": ["records"], "records_view": [records_view]}


def test_reference_records_include_every_node_direct_parent_and_only_own_active_values(
    dataset_browser,
):
    _, services, admin, client = dataset_browser
    h = populated_hierarchy(services, admin)
    login(client)

    response = request(client, "get", f"/datasets/{h.dataset.key}/concept/records?view=records")

    assert response.status_code == 200
    page = body(response)
    table = table_markup(page)
    assert headers(table) == ["Name", "Parent", "Code", "Owner", "Count", "Legacy", ""]
    assert table.count("data-hierarchy-node=") == 4
    assert "<span>4 records</span>" in page
    assert [visible_text(cell) for cell in row_cells(table, h.root)[:-1]] == [
        "Executive",
        "",
        "Not applicable",
        "Root owner",
        "11",
        "Not applicable",
    ]
    assert "No parent: root node" in row_cells(table, h.root)[1]
    assert [visible_text(cell) for cell in row_cells(table, h.branch)[:-1]] == [
        "Marketing",
        "Executive",
        "Not applicable",
        "Branch owner",
        "22",
        "Not applicable",
    ]
    assert [visible_text(cell) for cell in row_cells(table, h.child)[:-1]] == [
        "Content",
        "Marketing",
        "C",
        "Leaf owner",
        "33",
        "Empty",
    ]
    assert [visible_text(cell) for cell in row_cells(table, h.sibling)[:-1]] == [
        "HR",
        "Executive",
        "H",
        "Empty",
        "Empty",
        "Empty",
    ]
    assert "Inactive secret" not in page
    assert {parse_qs(urlsplit(url).query)["records_view"][0] for url in records_selector(page)} == {
        "reference",
        "flattened",
    }


def test_reference_records_show_a_root_without_children_or_fields(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_owned_dataset(
        services.datasets, admin.id, "root-only", "Root only", "", kind="hierarchy"
    )
    root = services.hierarchy.create_node(
        admin.id, dataset.key, "Root", expected_revision=dataset.revision
    )
    login(client)
    url = f"/datasets/{dataset.key}/concept/records?view=records"

    reference = body(request(client, "get", url))
    table = table_markup(reference)
    assert headers(table) == ["Name", "Parent", ""]
    assert [visible_text(cell) for cell in row_cells(table, root)[:-1]] == ["Root", ""]
    assert "<span>1 record</span>" in reference
    flattened = body(request(client, "get", url + "&records_view=flattened"))
    assert "No leaf records yet" in flattened
    assert "Records views" in flattened


def test_flattened_records_align_asymmetric_paths_and_keep_each_levels_values_separate(
    dataset_browser,
):
    _, services, admin, client = dataset_browser
    h = populated_hierarchy(services, admin)
    for node, name in ((h.root, "Executive / <root>"), (h.child, "Content / <leaf>")):
        h.hierarchy.update_node(
            admin.id,
            h.dataset.key,
            node.id,
            name,
            node.parent_id,
            expected_revision=services.datasets.get_dataset(admin.id, h.dataset.key).revision,
        )
    login(client)

    response = request(
        client,
        "get",
        f"/datasets/{h.dataset.key}/concept/records?view=records&records_view=flattened",
    )

    assert response.status_code == 200
    page = body(response)
    table = table_markup(page)
    assert headers(table) == [
        "Level 1",
        "Level 2",
        "Leaf",
        "Code Leaf",
        "Owner Level 1",
        "Owner Level 2",
        "Owner Leaf",
        "Count Level 1",
        "Count Level 2",
        "Count Leaf",
        "Legacy Leaf",
        "",
    ]
    assert table.count("data-hierarchy-node=") == 2
    assert [visible_text(cell) for cell in row_cells(table, h.child)[:-1]] == [
        "Executive / <root>",
        "Marketing",
        "Content / <leaf>",
        "C",
        "Root owner",
        "Branch owner",
        "Leaf owner",
        "11",
        "22",
        "33",
        "Empty",
    ]
    sibling_cells = row_cells(table, h.sibling)
    assert [visible_text(cell) for cell in sibling_cells[:-1]] == [
        "Executive / <root>",
        "",
        "HR",
        "H",
        "Root owner",
        "",
        "Empty",
        "11",
        "",
        "Empty",
        "Empty",
    ]
    for index in (1, 5, 8):
        assert "No node at this level" in sibling_cells[index]
        assert "Empty" not in sibling_cells[index]
    assert "Executive / &lt;root&gt;" in table and "Content / &lt;leaf&gt;" in table
    assert "<root>" not in page and "<leaf>" not in page
    assert "Inactive secret" not in page
    assert "Code Level" not in table and "Legacy Level" not in table


@pytest.mark.parametrize("records_view", ["reference", "flattened"])
def test_published_records_selector_and_panels_stay_on_selected_snapshot(
    dataset_browser, records_view
):
    _, services, admin, client = dataset_browser
    h = populated_hierarchy(services, admin)
    published = services.dataset_versions.publish(
        admin.id,
        h.dataset.id,
        datetime.now(UTC).date(),
        "Original hierarchy",
        services.datasets.get_dataset(admin.id, h.dataset.key).revision,
    )
    services.dataset_versions.create_concept(admin.id, h.dataset.id)
    h.hierarchy.update_node(
        admin.id,
        h.dataset.key,
        h.child.id,
        "Concept-only name",
        h.branch.id,
        expected_revision=services.datasets.get_dataset(admin.id, h.dataset.key).revision,
    )
    set_values(services, admin, h.dataset, h.hierarchy, h.branch, {"owner": "Concept-only owner"})
    login(client)
    path = f"/datasets/{h.dataset.key}/versions/{published.id}/records"

    response = request(client, "get", f"{path}?view=records&records_view={records_view}")

    assert response.status_code == 200
    page = body(response)
    table = table_markup(page)
    assert "Concept-only" not in page
    assert "Branch owner" in table
    assert "Inactive secret" not in page
    assert "/concept/nodes/" not in page
    assert "table-sticky-actions" not in table
    for link in records_selector(page):
        assert urlsplit(link).path == path
        assert parse_qs(urlsplit(link).query)["view"] == ["records"]
    expected_nodes = (
        (h.root, h.branch, h.child, h.sibling)
        if records_view == "reference"
        else (h.child, h.sibling)
    )
    assert page.count("<dialog class='side-panel' id='hierarchy-node-") == len(expected_nodes)
    for node in expected_nodes:
        assert f"data-dialog-open='hierarchy-node-{node.id}'" in table
        assert f"id='hierarchy-node-{node.id}'" in page
    assert "Executive / Marketing / Content" in page
    concept = body(
        request(
            client,
            "get",
            f"/datasets/{h.dataset.key}/concept/records?view=records&records_view={records_view}",
        )
    )
    assert "Concept-only name" in concept and "Concept-only owner" in concept


@pytest.mark.parametrize(
    ("records_view", "node_name"), [("reference", "branch"), ("flattened", "child")]
)
def test_record_value_edit_preserves_subview_on_success_validation_conflict_and_cancel(
    dataset_browser, records_view, node_name
):
    _, services, admin, client = dataset_browser
    h = populated_hierarchy(services, admin)
    node = getattr(h, node_name)
    login(client)
    page_url = f"/datasets/{h.dataset.key}/concept/records?view=records&records_view={records_view}"
    table = table_markup(body(request(client, "get", page_url)))
    cells = row_cells(table, node)
    action = cells[-1]
    assert "class='table-sticky-actions'" in table
    assert '<path d="M12 3H5' in action
    link = unescape(re.search(r"href='([^']+)'", action).group(1))
    assert parse_qs(urlsplit(link).query) == {
        "return_view": ["records"],
        "return_records_view": [records_view],
    }
    name_cell = cells[0] if records_view == "reference" else cells[2]
    assert unescape(re.search(r"href='([^']+)'", name_cell).group(1)) == link
    panel = request(client, "get", link)
    assert panel.status_code == 200
    assert_return_view(body(panel), h.dataset, records_view)
    data = {
        "csrf_token": session_csrf(client, services),
        "expected_revision": panel_revision(panel),
        "return_view": "records",
        "return_records_view": records_view,
        "field:owner": "Changed own value",
        "field:count": "not-an-integer",
    }
    invalid = request(client, "post", urlsplit(link).path, data=data)
    assert invalid.status_code == 400
    assert_return_view(body(invalid), h.dataset, records_view)
    assert "value='not-an-integer'" in body(invalid)
    data["field:count"] = "44"
    saved = request(client, "post", urlsplit(link).path, data=data)
    assert saved.status_code == 302
    assert saved.location == page_url
    updated_table = table_markup(body(request(client, "get", saved.location)))
    assert "Changed own value" in " ".join(row_cells(updated_table, node))
    stale = request(client, "post", urlsplit(link).path, data=data)
    assert stale.status_code == 409
    assert_return_view(body(stale), h.dataset, records_view)
    assert "Hierarchy changed since this form was opened" in body(stale)


@pytest.mark.parametrize("return_records_view", ["unknown", ["reference", "flattened"]])
def test_invalid_or_duplicate_return_records_view_cannot_mutate_values(
    dataset_browser, return_records_view
):
    _, services, admin, client = dataset_browser
    h = populated_hierarchy(services, admin)
    login(client)
    revision = services.datasets.get_dataset(admin.id, h.dataset.key).revision

    response = request(
        client,
        "post",
        f"/datasets/{h.dataset.key}/concept/nodes/{h.branch.id}/values",
        data={
            "csrf_token": session_csrf(client, services),
            "expected_revision": revision,
            "return_view": "records",
            "return_records_view": return_records_view,
            "field:owner": "Must not be saved",
        },
    )

    assert response.status_code == 400
    assert services.datasets.get_dataset(admin.id, h.dataset.key).revision == revision
    table = table_markup(
        body(request(client, "get", f"/datasets/{h.dataset.key}/concept/records?view=records"))
    )
    assert "Branch owner" in " ".join(row_cells(table, h.branch))
    assert "Must not be saved" not in table

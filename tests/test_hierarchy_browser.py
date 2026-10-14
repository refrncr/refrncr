"""REF-179 browser workflows, snapshot views and mutation safeguards."""

import re
from datetime import UTC, datetime

import pytest
from dataset_helpers import create_owned_dataset
from test_dataset_browser import (
    body,
    login,
    request,
    session_csrf,
)
from test_dataset_browser import dataset_browser as dataset_browser

from refrncr.hierarchy import HierarchyService
from refrncr.services import DatasetRecordService, ValidationError


def hierarchy_fixture(services, admin):
    dataset = create_owned_dataset(
        services.datasets, admin.id, "organisation", "Organisation", "", kind="hierarchy"
    )
    hierarchy = HierarchyService(services.datasets.uow, services.authorization)

    def add(name, parent=None):
        revision = services.datasets.get_dataset(admin.id, dataset.key).revision
        return hierarchy.create_node(
            admin.id, dataset.key, name, parent, expected_revision=revision
        )

    root = add("Executive")
    branch = add("Marketing", root.id)
    child = add("Content", branch.id)
    sibling = add("HR", root.id)
    return dataset, hierarchy, root, branch, child, sibling


def panel_revision(response):
    return re.search(r"name='expected_revision' value='(\d+)'", body(response)).group(1)


def test_hierarchy_creation_opens_structure_before_fields(dataset_browser):
    _, services, admin, client = dataset_browser
    login(client)
    form = request(client, "get", "/datasets/new?kind=hierarchy")
    assert "value='hierarchy' checked" in body(form)
    team = services.teams.list_teams(admin.id)[0]
    response = request(
        client,
        "post",
        "/datasets",
        data={
            "csrf_token": session_csrf(client, services),
            "owner_team_id": team.id,
            "key": "structure",
            "name": "Structure",
            "kind": "hierarchy",
        },
    )
    assert response.status_code == 302
    assert response.location.endswith("/datasets/structure/concept/records")
    page = request(client, "get", response.location)
    assert page.status_code == 200
    assert "Start with the root" in body(page)
    assert "Add root" in body(page)
    assert "Hierarchy views" in body(page)
    assert not services.dataset_fields.list_fields(admin.id, "structure")

    panel = request(client, "get", "/datasets/structure/concept/nodes/new")
    saved = request(
        client,
        "post",
        "/datasets/structure/concept/nodes/new",
        data={
            "csrf_token": session_csrf(client, services),
            "expected_revision": panel_revision(panel),
            "name": "Executive",
            "parent_id": "",
        },
    )
    assert saved.status_code == 302
    tree = body(request(client, "get", saved.location))
    assert "Executive" in tree
    assert "Add at least one child" in tree


def test_hierarchy_pages_show_export_menu(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, _hierarchy, _root, _branch, _child, _sibling = hierarchy_fixture(services, admin)
    login(client)

    page = body(request(client, "get", f"/datasets/{dataset.key}/concept/records"))

    assert "Export draft</summary>" in page
    assert f"/datasets/{dataset.key}/concept/records/export?format=csv_zip" in page
    assert f"/datasets/{dataset.key}/concept/records/export?format=xlsx" in page
    assert f"/datasets/{dataset.key}/concept/records/export?format=json" in page
    export = request(
        client,
        "get",
        f"/datasets/{dataset.key}/concept/records/export?format=xlsx",
    )
    assert export.status_code == 200
    assert export.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_export_validation_error_uses_a_handling_screen(dataset_browser, monkeypatch):
    _, services, admin, client = dataset_browser
    dataset, _hierarchy, _root, _branch, _child, _sibling = hierarchy_fixture(services, admin)
    login(client)

    def unavailable(*_args, **_kwargs):
        raise ValidationError("Hierarchy import is not available yet.")

    monkeypatch.setattr(DatasetRecordService, "export_dataset", unavailable)
    response = request(
        client,
        "get",
        f"/datasets/{dataset.key}/concept/records/export?format=xlsx",
    )

    page = body(response)
    assert response.status_code == 422
    assert "<h1>Export unavailable</h1>" in page
    assert "Your Dataset was not changed." in page
    assert f"href='/datasets/{dataset.key}/concept/records'" in page


def test_tree_and_table_show_the_complete_hierarchy(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, hierarchy, root, branch, child, sibling = hierarchy_fixture(services, admin)
    services.dataset_fields.create_field(
        admin.id, dataset.key, "code", "Code", "string", hierarchy_scope="leaves"
    )
    services.dataset_fields.create_field(
        admin.id, dataset.key, "owner", "Owner", "string", hierarchy_scope="all_nodes"
    )
    hierarchy.update_values(
        admin.id,
        dataset.key,
        branch.id,
        {"owner": "Marketing owner"},
        expected_revision=services.datasets.get_dataset(admin.id, dataset.key).revision,
    )
    login(client)
    url = f"/datasets/{dataset.key}/concept/records"
    tree = body(request(client, "get", url))
    assert "Marketing owner" in tree
    assert "class='hierarchy-table hierarchy-tree'" in tree
    assert "aria-label='Hierarchy views'" in tree
    assert "Tree" in tree and "Table" in tree
    assert all(
        f"data-hierarchy-node='{node.id}'" in tree for node in (root, branch, child, sibling)
    )

    table = body(request(client, "get", url + "?view=table"))
    assert "class='hierarchy-table hierarchy-table'" in table
    assert all(
        f"data-hierarchy-node='{node.id}'" in table for node in (root, branch, child, sibling)
    )
    assert "Marketing owner" in table
    assert "Leaves only" in table and "aria-label='Not applicable'" in table
    assert "Edit values for" not in table
    for old_view in ("hierarchy", "records", "hybrid"):
        response = request(client, "get", url + "?view=" + old_view)
        assert response.status_code == 301
        expected_view = "table" if old_view == "records" else "tree"
        assert f"view={expected_view}" in response.location

    panel = body(
        request(
            client,
            "get",
            f"/datasets/{dataset.key}/concept/nodes/{child.id}/values?return_view=records",
        )
    )
    assert "Executive / Marketing / Content" in panel
    assert "data-panel-title='Edit node'" in panel
    assert "name='field:code'" in panel


def test_node_edit_moves_subtree_and_retains_values(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, hierarchy, root, branch, child, sibling = hierarchy_fixture(services, admin)
    services.dataset_fields.create_field(
        admin.id, dataset.key, "owner", "Owner", "string", hierarchy_scope="all_nodes"
    )
    hierarchy.update_values(
        admin.id,
        dataset.key,
        child.id,
        {"owner": "Editor"},
        expected_revision=services.datasets.get_dataset(admin.id, dataset.key).revision,
    )
    login(client)
    url = f"/datasets/{dataset.key}/concept/nodes/{branch.id}/edit"
    panel = request(client, "get", url)
    assert f"<option value='{child.id}'" not in body(panel)
    response = request(
        client,
        "post",
        url,
        data={
            "csrf_token": session_csrf(client, services),
            "expected_revision": panel_revision(panel),
            "name": "Communications",
            "parent_id": sibling.id,
        },
    )
    assert response.status_code == 302
    details = body(
        request(client, "get", f"/datasets/{dataset.key}/concept/nodes/{child.id}/values")
    )
    assert "Executive / HR / Communications / Content" in details
    assert "value='Editor'" in details


def test_field_scope_controls_and_inactive_values(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, hierarchy, root, branch, child, sibling = hierarchy_fixture(services, admin)
    login(client)
    csrf = session_csrf(client, services)
    response = request(
        client,
        "post",
        f"/datasets/{dataset.key}/fields",
        data={
            "csrf_token": csrf,
            "key": "owner",
            "name": "Owner",
            "data_type": "string",
            "hierarchy_scope": "all_nodes",
        },
    )
    assert response.status_code == 302
    url = f"/datasets/{dataset.key}/concept/nodes/{branch.id}/values"
    panel = request(client, "get", url)
    saved = request(
        client,
        "post",
        url,
        data={
            "csrf_token": csrf,
            "expected_revision": panel_revision(panel),
            "field:owner": "Retained owner",
        },
    )
    assert saved.status_code == 302
    changed = request(
        client,
        "post",
        f"/datasets/{dataset.key}/fields/owner",
        data={
            "csrf_token": csrf,
            "name": "Owner",
            "data_type": "string",
            "hierarchy_scope": "leaves",
        },
    )
    assert changed.status_code == 302
    inactive = body(request(client, "get", url))
    assert "Inactive values" in inactive and "Retained owner" in inactive
    assert "name='field:owner'" not in inactive
    config = body(request(client, "get", f"/datasets/{dataset.key}/concept"))
    assert "<th scope='col'>Scope</th>" in config
    assert "Leaves only" in config
    assert "No record identifier configured" not in config


def test_hierarchy_node_mutations_reject_csrf_foreign_ids_and_stale_forms(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, hierarchy, root, branch, child, sibling = hierarchy_fixture(services, admin)
    login(client)
    url = f"/datasets/{dataset.key}/concept/nodes/{branch.id}/edit"
    panel = request(client, "get", url)
    payload = {"expected_revision": panel_revision(panel), "name": "Updated", "parent_id": root.id}
    assert request(client, "post", url, data=payload).status_code == 403
    payload["csrf_token"] = session_csrf(client, services)
    assert request(client, "post", url, data=payload).status_code == 302
    stale = request(client, "post", url, data=payload)
    assert stale.status_code == 409
    assert "Hierarchy changed since this form was opened" in body(stale)
    assert (
        request(
            client, "get", f"/datasets/{dataset.key}/concept/nodes/foreign-node/values"
        ).status_code
        == 404
    )


def test_branch_deletion_requires_confirmation_and_removes_the_complete_subtree(
    dataset_browser,
):
    _, services, admin, client = dataset_browser
    dataset, hierarchy, root, branch, child, sibling = hierarchy_fixture(services, admin)
    services.dataset_fields.create_field(
        admin.id, dataset.key, "owner", "Owner", "string", hierarchy_scope="all_nodes"
    )
    hierarchy.update_values(
        admin.id,
        dataset.key,
        child.id,
        {"owner": "Sensitive value"},
        expected_revision=services.datasets.get_dataset(admin.id, dataset.key).revision,
    )
    login(client)
    page = body(request(client, "get", f"/datasets/{dataset.key}/concept/records"))
    assert f"aria-label='Delete branch {root.name}'" not in page
    assert f"aria-label='Delete branch {branch.name}'" in page

    root_delete = request(client, "get", f"/datasets/{dataset.key}/concept/nodes/{root.id}/delete")
    assert root_delete.status_code == 400
    url = f"/datasets/{dataset.key}/concept/nodes/{branch.id}/delete"
    panel = request(client, "get", url)
    assert panel.status_code == 200
    assert "This permanently deletes 2 nodes" in body(panel)
    assert "Published versions remain unchanged" in body(panel)
    csrf = session_csrf(client, services)
    revision = panel_revision(panel)
    assert (
        request(
            client,
            "post",
            url,
            data={"csrf_token": csrf, "expected_revision": revision},
        ).status_code
        == 400
    )
    assert (
        request(
            client,
            "post",
            url,
            data={"expected_revision": revision, "confirm": "yes"},
        ).status_code
        == 403
    )
    deleted = request(
        client,
        "post",
        url,
        data={"csrf_token": csrf, "expected_revision": revision, "confirm": "yes"},
    )
    assert deleted.status_code == 302
    resulting_page = body(request(client, "get", deleted.location))
    assert branch.name not in resulting_page and child.name not in resulting_page
    assert root.name in resulting_page and sibling.name in resulting_page


def test_branch_deletion_rejects_stale_and_unauthorized_requests(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, hierarchy, root, branch, child, _sibling = hierarchy_fixture(services, admin)
    login(client)
    url = f"/datasets/{dataset.key}/concept/nodes/{branch.id}/delete"
    panel = request(client, "get", url)
    revision = panel_revision(panel)
    hierarchy.update_node(
        admin.id,
        dataset.key,
        child.id,
        "Updated content",
        branch.id,
        expected_revision=services.datasets.get_dataset(admin.id, dataset.key).revision,
    )
    stale = request(
        client,
        "post",
        url,
        data={
            "csrf_token": session_csrf(client, services),
            "expected_revision": revision,
            "confirm": "yes",
        },
    )
    assert stale.status_code == 409
    assert "Hierarchy changed since this form was opened" in body(stale)

    services.users.create_user(
        admin.id, "delete-reader@example.com", "Delete reader", "correct horse battery staple"
    )
    login(client, "delete-reader@example.com")
    assert request(client, "get", url).status_code == 404
    assert (
        request(
            client,
            "post",
            url,
            data={
                "csrf_token": session_csrf(client, services),
                "expected_revision": str(
                    services.datasets.get_dataset(admin.id, dataset.key).revision
                ),
                "confirm": "yes",
            },
        ).status_code
        == 404
    )


def test_published_views_remain_read_only_and_use_selected_snapshot(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, hierarchy, root, branch, child, sibling = hierarchy_fixture(services, admin)
    published = services.dataset_versions.publish(
        admin.id,
        dataset.id,
        datetime.now(UTC).date(),
        "Structure",
        services.datasets.get_dataset(admin.id, dataset.key).revision,
    )
    login(client)
    url = f"/datasets/{dataset.key}/versions/{published.id}/records"
    for view in ("hierarchy", "records", "hybrid"):
        response = request(client, "get", url + "?view=" + view, follow_redirects=True)
        assert response.status_code == 200
        page = body(response)
        assert "Published version · read only" in page
        assert "aria-label='Hierarchy views'" in page
        assert "/concept/nodes/" not in page
    services.dataset_versions.create_concept(admin.id, dataset.id)
    hierarchy.update_node(
        admin.id,
        dataset.key,
        child.id,
        "New content",
        branch.id,
        expected_revision=services.datasets.get_dataset(admin.id, dataset.key).revision,
    )
    assert "New content" not in body(request(client, "get", url + "?view=tree"))
    assert "New content" in body(
        request(client, "get", f"/datasets/{dataset.key}/concept/records?view=tree")
    )


@pytest.mark.parametrize(
    "view",
    [
        "unknown",
        "records&view=hybrid",
        "records&records_view=unknown",
        "records&records_view=reference&records_view=flattened",
    ],
)
def test_invalid_hierarchy_view_is_rejected(dataset_browser, view):
    _, services, admin, client = dataset_browser
    dataset, *_ = hierarchy_fixture(services, admin)
    login(client)
    assert (
        request(client, "get", f"/datasets/{dataset.key}/concept/records?view={view}").status_code
        == 400
    )


def test_deep_hierarchy_renders_without_recursive_templates(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_owned_dataset(
        services.datasets, admin.id, "deep", "Deep structure", "", kind="hierarchy"
    )
    with services.datasets.uow(write=True, serialized=True) as work:
        parent_id = None
        for depth in range(350):
            parent_id = work.hierarchy.create(dataset.id, f"Node {depth}", parent_id).id
    login(client)
    tree = request(client, "get", "/datasets/deep/concept/records")
    assert tree.status_code == 200
    assert "Node 349" in body(tree)
    assert body(tree).count("data-hierarchy-node=") == 350
    assert body(tree).count('data-icon="corner-down-right"') == 349
    records = request(
        client, "get", "/datasets/deep/concept/records?view=records", follow_redirects=True
    )
    assert records.status_code == 200
    assert body(records).count("data-hierarchy-node=") == 350
    assert "<th scope='col'>Level</th>" in body(records)
    assert "Node 0" in body(records)
    flattened = request(
        client,
        "get",
        "/datasets/deep/concept/records?view=records&records_view=flattened",
        follow_redirects=True,
    )
    assert flattened.status_code == 200
    assert body(flattened).count("data-hierarchy-node=") == 350


def test_custom_field_keys_cannot_collide_with_hierarchy_form_controls(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, _, _, _, child, _ = hierarchy_fixture(services, admin)
    for key in ("csrf_token", "expected_revision", "return_view", "return_records_view"):
        services.dataset_fields.create_field(
            admin.id, dataset.key, key, key, "string", hierarchy_scope="leaves"
        )
    login(client)
    url = f"/datasets/{dataset.key}/concept/nodes/{child.id}/values"
    panel = request(client, "get", url)
    response = request(
        client,
        "post",
        url,
        data={
            "csrf_token": session_csrf(client, services),
            "expected_revision": panel_revision(panel),
            "return_view": "records",
            "return_records_view": "flattened",
            "field:csrf_token": "ordinary-data",
            "field:expected_revision": "ordinary-revision-data",
            "field:return_view": "ordinary-view-data",
            "field:return_records_view": "ordinary-records-view-data",
        },
    )
    assert response.status_code == 302
    assert response.location.endswith("?view=table")
    saved = body(request(client, "get", url))
    assert "value='ordinary-data'" in saved
    assert "value='ordinary-revision-data'" in saved
    assert "value='ordinary-view-data'" in saved
    assert "value='ordinary-records-view-data'" in saved


def test_user_without_dataset_maintenance_cannot_open_or_submit_node_panels(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset, _, root, branch, _, _ = hierarchy_fixture(services, admin)
    services.users.create_user(
        admin.id, "reader@example.com", "Reader", "correct horse battery staple"
    )
    login(client, "reader@example.com")
    csrf = session_csrf(client, services)
    for path in ("new", f"{branch.id}/edit", f"{branch.id}/values"):
        url = f"/datasets/{dataset.key}/concept/nodes/{path}"
        assert request(client, "get", url).status_code == 404
        assert (
            request(
                client,
                "post",
                url,
                data={
                    "csrf_token": csrf,
                    "expected_revision": "1",
                    "name": "Forbidden",
                    "parent_id": root.id,
                },
            ).status_code
            == 404
        )

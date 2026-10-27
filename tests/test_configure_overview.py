"""REF-142 configuration presentation over existing authorized service reads."""

import re
from dataclasses import replace
from html import escape
from types import SimpleNamespace

import pytest
from dataset_helpers import publish_dataset
from support import (
    body,
    create_dataset,
    html_elements,
    html_tag_texts,
    login,
    request,
    session_csrf,
)


def section(page, heading):
    return re.search(
        rf"<section[^>]*aria-labelledby='{heading}'[^>]*>(.*?)</section>", page, re.S
    ).group(1)


@pytest.mark.parametrize("permission", ["viewer", "member", "community_admin"])
@pytest.mark.parametrize("count", [0, 1, 3])
def test_overview_structure_permissions_and_order_boundaries(dataset_browser, permission, count):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "overview", "Reference data")
    fields = [
        services.dataset_fields.create_field(
            admin.id, dataset.key, key, name, "string", index != 1, index == 0, index != 1
        )
        for index, (key, name) in enumerate(
            [("zeta", "First field"), ("alpha", "Middle field"), ("beta", "Last field")][:count]
        )
    ]
    actor = services.users.create_user(admin.id, "actor@example.com", "Actor", "user password")
    if permission == "member":
        services.memberships.create_membership(admin.id, actor.id, dataset.owner_team_id)
    if permission == "community_admin":
        login(client)
    else:
        login(client, actor.email, "user password")
    version = (
        publish_dataset(services, admin.id, dataset) if permission == "viewer" and count else None
    )
    url = "/datasets/overview" + ("" if permission == "viewer" else "/concept")
    response = request(client, "get", url)
    if permission == "viewer" and not count:
        assert response.status_code == 404
        assert b'<h1 id="error-title">The requested page could not be found.</h1>' in response.data
        return
    assert response.status_code == 200
    page = body(response)
    headings = re.findall(r"<h1[^>]*>(.*?)</h1>", page, re.DOTALL)
    assert [re.sub(r"<[^>]+>", "", h).strip() for h in headings] == ["Reference data"]
    assert '<nav class="breadcrumbs" aria-label="Breadcrumb">' in page
    assert '<div class="page-context">' in page
    assert "0 records</span>" in page and "Updated <time" in page
    for section_id in (
        "dataset-details",
        "dataset-general",
        "dataset-ownership",
        "dataset-identification",
        "fields",
    ):
        assert f"id='{section_id}'" in page
    assert ("id='danger-zone'" in page) == (permission != "viewer")
    assert ("id='access-management'" in page) == (permission in {"community_admin", "member"})
    header = page.split("<div class='configuration-overview' data-tab-panels>")[0]
    assert "<div class='page-header page-header-actions-end'>" in header
    actions = header.split("<div class='page-actions record-page-actions'>")[1]
    records_url = (
        f"/datasets/overview/versions/{version.id}/records"
        if version
        else "/datasets/overview/concept/records"
    )
    assert f"class='button button-secondary' href='{records_url}'>View records" not in actions
    assert "Edit dataset" not in actions
    general = section(page, "dataset-general-heading")
    ownership = section(page, "dataset-ownership-heading")
    identification = section(page, "dataset-identification-heading")
    assert "Edit dataset" not in page
    assert ("edit=general" in general) == (permission != "viewer")
    assert ("edit=ownership" in ownership) == (permission == "community_admin")
    for label in ("Name", "Description", "Icon"):
        assert f"<dt>{label}</dt>" in general
    assert "<dt>Owning team</dt>" in ownership
    for label in ("Technical name", "Type"):
        assert f"<dt>{label}</dt>" in identification
    assert "At a glance" not in page
    assert "<dt>Version</dt>" not in page and "<dt>Created</dt>" not in page
    assert "<dt>Fields</dt>" not in page
    assert "<p class='dataset-identity-name'>Reference data</p>" not in page
    assert "</svg>Test data domain</span>" in ownership
    assert "No description provided" in general
    expected_key_status = (
        "Permanent" if permission == "viewer" else "Editable until first publication"
    )
    assert expected_key_status in identification
    assert "<dt>Records</dt>" not in page
    assert "0 records</span>" in page
    assert "They are not part of a version." not in page
    assert "How the dataset appears in navigation, search and exports." in general
    assert "Fixed after the first publication." in identification
    assert "Used in URLs, exports and the API." in identification
    # UUIDs remain legal in functional hidden inputs and access subject values.
    presentation = re.sub(
        r"<input[^>]*type='hidden'[^>]*>| value='[^']*'| href='/datasets/id/[^']*'", "", page
    )
    for internal_id in (dataset.id, admin.id, *(field.id for field in fields)):
        assert internal_id not in presentation
    field_section = section(page, "fields-heading")
    assert not re.search(r"draggable|ondrag\w*|ondrop|data-drag", field_section, re.IGNORECASE)
    assert "<h2 id='fields-heading'>Fields</h2>" in field_section
    assert ("Add field" in field_section) == (permission != "viewer")
    assert ("configuration-panel" in page) == (version is not None or permission != "viewer")
    assert "record-identifier-heading" not in page
    assert "No record identifier configured" not in page
    if not count:
        assert "<table" not in field_section
        assert "No fields yet" in field_section
        assert "Fields describe the values each record can contain." in field_section
        return
    assert "<caption class='visually-hidden'>Dataset fields</caption>" in field_section
    assert "role='region' aria-label='Dataset fields' tabindex='0'" in field_section
    assert re.findall(
        r"<th scope='col'[^>]*>(?:<span[^>]*>)?(.*?)(?:</span>)?</th>", field_section
    ) == ([] if permission == "viewer" else ["Field order"]) + ["Field", "Type", "Constraints"] + (
        [] if permission == "viewer" else ["Actions"]
    )
    assert "<span class='badge'>Required</span>" in field_section
    assert "<span class='badge'>Unique</span>" in field_section
    identifier_count = 1 if count == 1 else 2
    identifier_badge = "<span class='badge badge-identifier'>Identifier</span>"
    assert field_section.count(identifier_badge) == identifier_count
    assert (
        sum(
            tag == "svg" and "key-icon" in attrs.get("class", "").split()
            for tag, attrs in html_elements(field_section)
        )
        == identifier_count
    )
    rows = re.findall(r"<tr[^>]*>\s*<td[^>]*>(.*?)</tr>", field_section, re.DOTALL)
    assert len(rows) == count
    assert field_section.count("data-row-action class='interactive-row'") == (
        count if permission == "viewer" else 0
    )
    assert field_section.count("data-row-primary") == (count if permission == "viewer" else 0)
    for index, (field, row) in enumerate(zip(fields, rows, strict=True)):
        if permission == "viewer":
            assert (
                "<a class='row-action field-version-details' data-row-primary" in row
                and f"<span class='field-name row-action-label'>{field.name}</span>" in row
            )
        else:
            assert f"<td class='field-identity'><span class='field-name'>{field.name}</span>" in row
        assert f"<code class='field-key'>{field.key}</code></td>" in row
        cells = row.split("</td>")
        order_cell, indicator = (cells[0], cells[1]) if permission != "viewer" else ("", row)
        if permission != "viewer":
            assert "key-icon" not in order_cell
        if index != 1:
            if permission == "viewer":
                assert f"{field.name}</span> <svg" in indicator
                primary_link = row.split("<a class='row-action field-version-details", 1)[1].split(
                    "</a>", 1
                )[0]
                assert '<svg class="key-icon"' in primary_link
            else:
                assert f"{field.name}</span> <svg" in indicator
            assert '<svg class="key-icon"' in indicator
            assert identifier_badge in row
            assert f"{field.name} is part of the record identifier" in indicator
        else:
            assert "key-icon" not in indicator
        if permission != "viewer":
            assert f"aria-label='Edit field {field.name}' data-panel-url" in row
            edit = re.search(r"<a class='field-edit-action'[^>]*>(.*?)</a>", row, re.DOTALL)
            assert edit is not None
            assert '<svg class="button-icon"' in edit[1]
            assert 'aria-hidden="true"' in edit[1]
            assert not re.sub(r"<[^>]+>", "", edit[1]).strip()
            assert "class='button-stack'" not in order_cell
            assert "<button" not in row.rsplit("<td>", 1)[1]
            assert f"aria-label='Move {field.name} up'" not in row
            assert f"aria-label='Move {field.name} down'" not in row
            assert f"aria-label='Reorder {field.name}'" in order_cell
        else:
            assert "<form" not in row and "<button" not in row and "Edit field" not in row
            assert "field-position" not in row and "field-order-column" not in row
    if count == 3:
        assert "None" in rows[1] and "Identifier</span>" not in rows[1]
        assert field_section.index("First field") < field_section.index("Last field")
        # Presentation follows changed canonical display order, not alphabetical identifier keys.
        if permission == "viewer":
            services.dataset_versions.create_concept(admin.id, dataset.id)
        services.dataset_fields.replace_order(
            admin.id,
            dataset.key,
            tuple(f.id for f in fields),
            tuple(f.id for f in reversed(fields)),
        )
        reordered = body(request(client, "get", url))
        reordered_fields = section(reordered, "fields-heading")
        if permission == "viewer":
            assert reordered_fields.index("First field") < reordered_fields.index("Last field")
        else:
            assert reordered_fields.index("Last field") < reordered_fields.index("First field")


@pytest.mark.parametrize(
    ("tab", "title"),
    [("fields", "Fields"), ("access-management", "Access"), ("dataset-details", "Settings")],
)
def test_dataset_detail_browser_title_matches_active_tab(dataset_browser, tab, title):
    _, services, admin, client = dataset_browser
    create_dataset(services, admin, "title-tabs", "Countries")
    login(client)
    page = body(request(client, "get", f"/datasets/title-tabs/concept?tab={tab}"))
    assert f"<title>{title} · Countries · REFRNCR</title>" in page
    assert "View current version" not in html_tag_texts(page, "a")


def test_general_and_ownership_edits_are_separate_and_keep_technical_name_prepublication(
    dataset_browser,
):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "settings-edit", "Settings edit", "Original")
    login(client)

    general = request(client, "get", "/datasets/settings-edit/concept?edit=general")
    assert general.status_code == 200
    general_html = body(general)
    assert "name='edit_section' value='general'" in general_html
    assert "name='technical_key'" in general_html
    assert "name='owner_team_id'" not in general_html
    response = request(
        client,
        "post",
        "/datasets/settings-edit",
        data={
            "csrf_token": session_csrf(client, services),
            "edit_section": "general",
            "settings_return_context": "concept",
            "name": "Renamed dataset",
            "description": "Updated description",
            "technical_key": "renamed-dataset",
            "icon": "database",
        },
    )
    assert response.status_code == 302
    assert response.location.endswith(
        "/datasets/renamed-dataset/concept?tab=dataset-details#dataset-details"
    )
    updated = services.datasets.get_dataset_for_edit(admin.id, "renamed-dataset")
    assert (updated.name, updated.description, updated.icon) == (
        "Renamed dataset",
        "Updated description",
        "database",
    )

    ownership = request(client, "get", "/datasets/renamed-dataset/concept?edit=ownership")
    assert ownership.status_code == 200
    ownership_html = body(ownership)
    assert "name='edit_section' value='ownership'" in ownership_html
    assert "name='owner_team_id'" in ownership_html
    assert "name='name'" not in ownership_html
    assert "name='technical_key'" not in ownership_html
    response = request(
        client,
        "post",
        "/datasets/renamed-dataset",
        data={
            "csrf_token": session_csrf(client, services),
            "edit_section": "ownership",
            "owner_team_id": dataset.owner_team_id,
            "expected_owner_team_id": dataset.owner_team_id,
        },
    )
    assert response.status_code == 302
    retained = services.datasets.get_dataset_for_edit(admin.id, "renamed-dataset")
    assert (retained.name, retained.description, retained.icon, retained.key) == (
        "Renamed dataset",
        "Updated description",
        "database",
        "renamed-dataset",
    )


def test_cancel_general_edit_returns_to_the_same_dataset_settings_tabs(dataset_browser):
    _, services, admin, client = dataset_browser
    create_dataset(services, admin, "settings-cancel", "Settings cancel")
    login(client)

    response = request(
        client,
        "get",
        "/datasets/settings-cancel/concept?tab=dataset-details&edit=general",
    )

    assert response.status_code == 200
    page = body(response)
    assert "aria-label='Dataset sections'" in page
    cancel = re.search(r"<a class='button button-secondary' href='([^']+)'>Cancel</a>", page).group(
        1
    )
    assert cancel == "/datasets/settings-cancel/concept?tab=dataset-details#dataset-details", cancel

    saved = request(
        client,
        "post",
        "/datasets/settings-cancel",
        data={
            "csrf_token": session_csrf(client, services),
            "edit_section": "general",
            "settings_return_context": "concept",
            "name": "Settings saved",
            "description": "Updated",
            "icon": "database",
        },
    )
    assert saved.status_code == 302
    assert saved.location.endswith(
        "/datasets/settings-cancel/concept?tab=dataset-details#dataset-details"
    )


def test_long_and_malicious_metadata_stays_escaped(dataset_browser):
    _, services, admin, client = dataset_browser
    name = '<script>alert("metadata")</script>' + "N" * 150
    description = '<img src=x onerror="alert(1)">' + "D" * 9000
    dataset = create_dataset(services, admin, "k" * 100, name, description)
    field_name = '<img src=x onerror="alert(2)">' + "F" * 150
    services.dataset_fields.create_field(
        admin.id, dataset.key, "f" * 100, field_name, "string", True, False, True
    )
    login(client)
    page = body(request(client, "get", f"/datasets/{dataset.key}/concept"))
    assert name not in page and description not in page and field_name not in page
    for value in (name, description, field_name):
        assert escape(value).replace("&#x27;", "&#39;").replace("&quot;", "&#34;") in page
    elements = html_elements(page)
    assert "id='dataset-general'" in page and "id='dataset-identification'" in page
    assert sum(tag == "table" for tag, _ in elements) == 2
    assert "aria-label='Edit field &lt;img" in page

    field_section = section(page, "fields-heading")
    escaped_name = escape(field_name).replace("&quot;", "&#34;")
    assert escaped_name in field_section
    assert f"{escaped_name} is part of the record identifier" in page
    assert "f" * 100 in field_section


def test_identifier_template_escapes_untrusted_field_keys(dataset_browser, monkeypatch):
    # Defense in depth at the presentation boundary; domain key validation stays unchanged.
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "untrusted-key")
    malicious_key = '<script>alert("key")</script>'
    field = SimpleNamespace(
        id="internal-field-id",
        key=malicious_key,
        name="Identifier field",
        data_type="string",
        is_required=True,
        is_unique=False,
    )
    original_read = services.dataset_versions.read

    def untrusted_read(*args, **kwargs):
        return replace(
            original_read(*args, **kwargs),
            fields=(field,),
            identifier=SimpleNamespace(field_keys=(malicious_key,)),
        )

    monkeypatch.setattr(services.dataset_versions, "read", untrusted_read)
    login(client)
    page = body(request(client, "get", f"/datasets/{dataset.key}/concept"))
    assert malicious_key not in page
    escaped_key = escape(malicious_key).replace("&quot;", "&#34;")
    assert escaped_key in section(page, "fields-heading")
    assert "Identifier field is part of the record identifier" in page


@pytest.mark.parametrize("permission", ["viewer", "member", "community_admin"])
@pytest.mark.parametrize("state", ["empty", "unconfigured", "configured"])
def test_missing_identifier_warning_only_for_existing_fields(
    dataset_browser,
    permission,
    state,
):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "identifier-warning")
    if state != "empty":
        services.dataset_fields.create_field(admin.id, dataset.key, "code", "Code", "string", True)
    if state == "configured":
        services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    actor = services.users.create_user(admin.id, "actor@example.com", "Actor", "user password")
    if permission == "member":
        services.memberships.create_membership(admin.id, actor.id, dataset.owner_team_id)
    if permission == "community_admin":
        login(client)
    else:
        login(client, actor.email, "user password")
    if permission == "viewer" and state == "configured":
        publish_dataset(services, admin.id, dataset)
    response = request(
        client, "get", f"/datasets/{dataset.key}" + ("" if permission == "viewer" else "/concept")
    )
    if permission == "viewer" and state != "configured":
        assert response.status_code == 404
        assert b"No record identifier configured" not in response.data
        return
    assert response.status_code == 200
    page = body(response)
    assert "record-identifier-heading" not in page
    warning = re.search(r"<div class='alert alert-warning'[^>]*>(.*?)</div>", page, re.DOTALL)
    assert (warning is not None) == (state == "unconfigured")
    if warning is not None:
        assert "No record identifier configured." in warning[1]
        assert "Choose one or more fields to uniquely identify each record." in warning[1]
        assert "<a " not in warning[1] and "<button" not in warning[1]

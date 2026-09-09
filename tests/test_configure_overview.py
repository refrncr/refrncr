"""REF-142 configuration presentation over existing authorized service reads."""

import re
from html import escape
from types import SimpleNamespace

import pytest
from test_dataset_browser import (  # noqa: F401
    body,
    create_dataset,
    dataset_browser,
    login,
    request,
)


def section(page, heading):
    return page.split(f"<section aria-labelledby='{heading}'>", 1)[1].split("</section>", 1)[0]


@pytest.mark.parametrize("permission", ["viewer", "member", "community_admin"])
@pytest.mark.parametrize("count", [0, 1, 3])
def test_overview_structure_permissions_and_order_boundaries(dataset_browser, permission, count):  # noqa: F811
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
    response = request(client, "get", "/datasets/overview")
    assert response.status_code == 200
    page = body(response)
    assert re.findall(r"<h1[^>]*>(.*?)</h1>", page) == ["Reference data"]
    assert '<nav class="breadcrumbs" aria-label="Breadcrumb">' in page
    assert '<div class="page-context">' in page
    assert "0 records</span>" in page and "Updated <time" in page
    assert page.count("<section ") == 2
    header = page.split("<div class='configuration-overview'>")[0]
    assert "<div class='page-header page-header-actions-end'>" in header
    actions = header.split("<div class='page-actions record-page-actions'>")[1]
    assert "class='button button-primary' href='/datasets/overview/records'>View records" in actions
    assert ("Edit dataset" in actions) == (permission != "viewer")
    if permission != "viewer":
        assert "class='button button-secondary' data-panel-url" in actions
    details = section(page, "dataset-details-heading")
    for label in ("Name", "Description", "Owning team", "Key"):
        assert f"<dt>{label}</dt>" in details
    assert "<dd>Test data domain</dd>" in details
    assert "No description provided" in details
    assert "<dd><code>overview</code></dd>" in details
    # UUIDs remain legal only in functional hidden ordering inputs.
    presentation = re.sub(r"<input[^>]*type='hidden'[^>]*>", "", page)
    for internal_id in (dataset.id, admin.id, *(field.id for field in fields)):
        assert internal_id not in presentation
    field_section = section(page, "fields-heading")
    assert not re.search(r"draggable|ondrag\w*|ondrop|data-drag", field_section, re.IGNORECASE)
    assert f"{count} {'field' if count == 1 else 'fields'}</p>" in field_section
    assert ("Add field" in field_section) == (permission != "viewer")
    assert ("configuration-panel" in page) == (permission != "viewer")
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
    ) == [
        "Field order",
        "Field",
        "Key",
        "Type",
        "Constraints",
        "Actions",
    ]
    assert "<span class='badge'>Required</span>" in field_section
    assert "<span class='badge'>Unique</span>" in field_section
    identifier_count = 1 if count == 1 else 2
    assert field_section.count("<span class='badge'>Identifier</span>") == identifier_count
    assert field_section.count("images/lucide-key-round.svg") == identifier_count
    rows = re.findall(r"<tr>\s*<td[^>]*>(.*?)</tr>", field_section, re.DOTALL)
    assert len(rows) == count
    for index, (field, row) in enumerate(zip(fields, rows, strict=True)):
        assert f"<td><span class='field-name'>{field.name}</span>" in row
        assert f"<td><code class='field-key'>{field.key}</code></td>" in row
        order_cell, indicator = row.split("</td>")[:2]
        assert "key-icon" not in order_cell
        if index != 1:
            assert f"{field.name}</span> <img" in indicator
            assert "images/lucide-key-round.svg" in indicator
            assert "<span class='badge'>Identifier</span>" in row
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
            assert "class='button-stack'" in order_cell
            assert "<button" not in row.rsplit("<td>", 1)[1]
            for direction, boundary in (("up", index == 0), ("down", index == count - 1)):
                assert f"aria-label='Move {field.name} {direction}'" in row
                control = re.search(rf"<button[^>]*value='{direction}'([^>]*)>", row)
                assert control is not None
                assert ("disabled" in control[1]) == boundary
        else:
            assert "<form" not in row and "<button" not in row and "Edit field" not in row
    if count == 3:
        assert "None" in rows[1] and "<span class='badge'>" not in rows[1]
        assert field_section.index("First field") < field_section.index("Last field")
        # Presentation follows changed canonical display order, not alphabetical identifier keys.
        services.dataset_fields.replace_order(
            admin.id,
            dataset.key,
            tuple(f.id for f in fields),
            tuple(f.id for f in reversed(fields)),
        )
        reordered = body(request(client, "get", "/datasets/overview"))
        reordered_fields = section(reordered, "fields-heading")
        assert reordered_fields.index("Last field") < reordered_fields.index("First field")


def test_long_and_malicious_metadata_stays_escaped(dataset_browser):  # noqa: F811
    _, services, admin, client = dataset_browser
    name = '<script>alert("metadata")</script>' + "N" * 150
    description = '<img src=x onerror="alert(1)">' + "D" * 9000
    dataset = create_dataset(services, admin, "k" * 100, name, description)
    field_name = '<img src=x onerror="alert(2)">' + "F" * 150
    services.dataset_fields.create_field(
        admin.id, dataset.key, "f" * 100, field_name, "string", True, False, True
    )
    login(client)
    page = body(request(client, "get", f"/datasets/{dataset.key}"))
    assert name not in page and description not in page and field_name not in page
    for value in (name, description, field_name):
        assert escape(value).replace("&#x27;", "&#39;").replace("&quot;", "&#34;") in page
    assert page.count("<section ") == 2
    assert page.count("<table>") == 1
    assert "aria-label='Edit field &lt;img" in page

    field_section = section(page, "fields-heading")
    escaped_name = escape(field_name).replace("&quot;", "&#34;")
    assert escaped_name in field_section
    assert f"{escaped_name} is part of the record identifier" in page
    assert "f" * 100 in field_section


def test_identifier_template_escapes_untrusted_field_keys(dataset_browser, monkeypatch):  # noqa: F811
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
    monkeypatch.setattr(services.dataset_fields, "list_fields", lambda *_: [field])
    monkeypatch.setattr(
        services.dataset_business_keys,
        "get_business_key",
        lambda *_: SimpleNamespace(field_keys=(malicious_key,)),
    )
    login(client)
    page = body(request(client, "get", f"/datasets/{dataset.key}"))
    assert malicious_key not in page
    escaped_key = escape(malicious_key).replace("&quot;", "&#34;")
    assert escaped_key in section(page, "fields-heading")
    assert "Identifier field is part of the record identifier" in page


@pytest.mark.parametrize("permission", ["viewer", "member", "community_admin"])
@pytest.mark.parametrize("state", ["empty", "unconfigured", "configured"])
def test_missing_identifier_warning_only_for_existing_fields(
    dataset_browser,  # noqa: F811
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
    page = body(request(client, "get", f"/datasets/{dataset.key}"))
    assert "record-identifier-heading" not in page
    warning = re.search(r"<div class='alert alert-warning'[^>]*>(.*?)</div>", page, re.DOTALL)
    assert (warning is not None) == (state == "unconfigured")
    if warning is not None:
        assert "No record identifier configured." in warning[1]
        assert "Choose one or more fields to uniquely identify each record." in warning[1]
        assert "<a " not in warning[1] and "<button" not in warning[1]

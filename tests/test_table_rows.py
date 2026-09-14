"""Shared row composition and progressive-enhancement contracts."""

import re
import shutil
import subprocess
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader
from test_dataset_browser import (  # noqa: F401
    body,
    create_dataset,
    create_field,
    dataset_browser,
    login,
    request,
)

ROOT = Path(__file__).parents[1]


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


@pytest.mark.parametrize("panel", [False, True])
def test_shared_row_has_one_primary_control_and_escapes_values(panel):
    env = Environment(loader=FileSystemLoader(ROOT / "src/refrncr/templates"), autoescape=True)
    template = env.from_string("""
        {% from 'components/table_row.html' import table_row, row_action %}
        <table><caption>Items</caption><thead><tr><th scope='col'>Name</th>
        <th scope='col'>Actions</th></tr></thead><tbody>
        {% call table_row(panel=panel, search_text=name) %}
          <td>{{ row_action(name, href=none if panel else url,
                           panel=panel_id if panel else none, accessible_name=name) }}</td>
          <td><button type='button'>Other action</button></td>
        {% endcall %}</tbody></table>
    """)
    name = "Long" * 150 + " <script>alert('x')</script> & \"name\""
    url = "/datasets/example?name='unsafe'&page=2"
    html = template.render(panel=panel, name=name, url=url, panel_id="item-'unsafe'")
    elements = Elements(html).elements
    primaries = [(tag, attrs) for tag, attrs in elements if "data-row-primary" in attrs]
    assert len(primaries) == 1
    tag, attrs = primaries[0]
    assert tag == ("button" if panel else "a")
    assert attrs["aria-label"] == name
    assert attrs.get("href") == (None if panel else url)
    if panel:
        assert attrs["type"] == "button"
        assert attrs["data-dialog-open"] == "item-'unsafe'"
        assert attrs["aria-controls"] == attrs["data-dialog-open"]
        assert attrs["aria-haspopup"] == "dialog"
    rows = [attrs for tag, attrs in elements if tag == "tr"]
    assert all("tabindex" not in attrs and "role" not in attrs for attrs in rows)
    assert "data-row-action" not in rows[0]
    assert rows[1]["data-search-text"] == name
    icons = [attrs for tag, attrs in elements if tag == "svg"]
    assert len(icons) == 1
    assert icons[0]["aria-hidden"] == "true"
    assert icons[0]["focusable"] == "false"
    assert all(tag != "script" for tag, _ in elements)
    assert ">View<" not in html
    assert "<th scope='col'>Actions</th>" in html  # Other legitimate actions survive.
    assert "<button type='button'>Other action</button>" in html


def test_shared_table_row_controller():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable; native browser checks remain required")
    result = subprocess.run(
        [
            node,
            str(ROOT / "tests/javascript/table_rows.cjs"),
            str(ROOT / "src/refrncr/static/js/refrncr.js"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "kind,columns", [("datasets", 5), ("teams", 3), ("users", 5), ("records", 3)]
)
def test_rendered_lists_share_semantics_destinations_and_escaping(dataset_browser, kind, columns):  # noqa: F811
    _, services, admin, client = dataset_browser
    name = "Long" * 15 + " <img src=x onerror=alert(1)> & 'item'"
    dataset = create_dataset(services, admin, "row-contract", name)
    create_field(services, admin, dataset, "description", "Description")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    record = services.dataset_records.create_record(admin.id, dataset.key, {"code": name})
    team = services.teams.create_team(admin.id, name)
    user = services.users.create_user(admin.id, "row@example.com", name, "valid password")
    path = f"/datasets/{dataset.key}/concept/records" if kind == "records" else f"/{kind}"
    login(client)
    html = body(request(client, "get", path))
    table = re.search(r"<table\b[^>]*>(.*?)</table>", html, re.S).group(1)
    headers = re.findall(r"<th\b([^>]*)>(.*?)</th>", table, re.S)
    assert len(headers) == columns
    assert all("scope='col'" in attrs for attrs, _ in headers)
    assert "Actions" not in table and ">View<" not in table
    assert "<caption" in table
    assert all(tag != "img" for tag, _ in Elements(table).elements if kind != "records")
    assert "onerror" not in [key for _, attrs in Elements(table).elements for key in attrs]
    rows = re.findall(r"<tr\b([^>]*)>(.*?)</tr>", table, re.S)[1:]
    targets = []
    for attrs, contents in rows:
        assert "data-row-action" in attrs
        assert "tabindex" not in attrs and "role='button'" not in attrs
        elements = Elements(contents).elements
        controls = [(tag, attrs) for tag, attrs in elements if tag in ("a", "button", "input")]
        if kind == "datasets":
            assert len(controls) == 3
            assert controls[2][1]["href"] == "/datasets/row-contract/configure"
            assert controls[2][1]["title"] == "configuration"
            assert controls[1][1]["href"] == "/datasets/row-contract/versions"
        else:
            assert len(controls) == 1
        tag, control = controls[0]
        assert "data-row-primary" in control
        assert tag == ("a" if kind in ("datasets", "teams") else "button")
        assert len([tag for tag, _ in elements if tag == "td"]) == columns
        assert len([tag for tag, _ in elements if tag == "svg"]) == (2 if kind == "datasets" else 1)
        assert all(
            a["aria-hidden"] == "true" and a["focusable"] == "false"
            for tag, a in elements
            if tag == "svg"
        )
        if name in unescape(contents):
            targets.append(control)
    assert len(targets) == 1
    control = targets[0]
    if kind == "datasets":
        assert control["href"] == "/datasets/row-contract/concept/records"
        assert name in unescape(table)
    elif kind == "teams":
        assert control["href"] == f"/teams?open_team={team.id}"
    else:
        assert control["data-dialog-open"] == (
            f"user-panel-{user.id}" if kind == "users" else f"record-panel-{record.id}"
        )
    if kind != "datasets":
        assert name in control["aria-label"]


def test_empty_and_configuration_tables_do_not_adopt_row_actions(dataset_browser):  # noqa: F811
    _, services, admin, client = dataset_browser
    login(client)
    for team in services.teams.list_teams(admin.id):
        services.teams.delete_team(admin.id, team.id)
    for path in ("/datasets", "/teams"):
        assert "data-row-action" not in body(request(client, "get", path))
    dataset = create_dataset(services, admin, "empty-rows")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    assert "data-row-action" not in body(
        request(client, "get", "/datasets/empty-rows/concept/records")
    )
    html = body(request(client, "get", "/datasets/empty-rows/concept"))
    assert "data-row-action" not in html
    assert "<th scope='col'>Actions</th>" in html
    assert "aria-label='Edit field Code'" in html
    assert "aria-label='Move Code up'" in html
    env = client.application.jinja_env
    preview = env.get_template("pages/datasets/import/preview.html").render(
        state={"filename": "example.csv"},
        source={"rows": [["value"]]},
        preview={"valid": True},
        preview_rows=[["<script>value</script>"]],
        preview_fields=[{"name": "Code"}],
    )
    assert "data-row-action" not in preview and "row-chevron" not in preview
    assert "&lt;script&gt;value&lt;/script&gt;" in preview


def test_numeric_identifier_primary_name_includes_visible_formatted_value(dataset_browser):  # noqa: F811
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "numeric-rows")
    create_field(services, admin, dataset, "code", "Code", "number", is_required=True)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    for value in ("1.2", "1.234"):
        services.dataset_records.create_record(admin.id, dataset.key, {"code": value})
    login(client)
    html = body(request(client, "get", "/datasets/numeric-rows/concept/records"))
    controls = re.findall(r"<button\b([^>]*data-row-primary[^>]*)>(.*?)</button>", html, re.S)
    assert len(controls) == 2
    for attrs, content in controls:
        label = re.search(r"class='row-action-label'>(.*?)</span>", content, re.S).group(1).strip()
        assert label in unescape(re.search(r"aria-label='([^']*)'", attrs).group(1))

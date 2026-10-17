"""The Select component: server-rendered native select that select.js enhances."""

import shutil
import subprocess
from pathlib import Path

import pytest
from support import body, html_elements, login, request

ROOT = Path(__file__).parents[1]


@pytest.fixture
def render(dataset_browser):
    env = dataset_browser[3].application.jinja_env

    def render(call, **values):
        template = env.from_string("{% from 'components.html' import select_field %}" + call)
        return template.render(**values)

    return render


def elements(html, tag):
    return [attrs for name, attrs in html_elements(html) if name == tag]


def test_select_field_renders_a_labelled_native_select_marked_for_enhancement(render):
    html = render(
        "{{ select_field('Country', 'country', 'country', options, selected='NL',"
        " placeholder='Choose a country', help_text='Used for exports.', required=true) }}",
        options=[("BE", "Belgium"), ("NL", "Netherlands")],
    )
    (label,) = elements(html, "label")
    (select,) = elements(html, "select")
    options = elements(html, "option")
    assert label["for"] == select["id"] == "country"
    assert select["name"] == "country"
    assert select["data-select"] == ""
    assert "required" in select
    assert select["aria-describedby"] == "country-help"
    assert [option["value"] for option in options] == ["", "BE", "NL"]
    assert "selected" in options[2] and "selected" not in options[1]
    assert "Used for exports." in html


def test_select_field_describes_errors_variants_and_option_metadata(render):
    html = render(
        "{{ select_field('Status', 'status', 'status', options, error='Choose a status.',"
        " described_by='status-note', compact=true, searchable=false, noun='statuses',"
        " disabled=true) }}",
        options=[
            {"value": "draft", "label": "Draft", "badge": "draft"},
            {"value": "published", "label": "Published", "badge": "published", "code": "P"},
            {"value": "removed", "label": "Removed", "disabled": True},
        ],
    )
    (select,) = elements(html, "select")
    options = elements(html, "option")
    assert select["aria-describedby"] == "status-error status-note"
    assert select["aria-invalid"] == "true"
    assert select["data-select"] == "compact"
    assert select["data-searchable"] == "false"
    assert select["data-noun"] == "statuses"
    assert "disabled" in select
    assert options[0]["data-badge"] == "draft"
    assert options[1]["data-code"] == "P"
    assert "disabled" in options[2] and "disabled" not in options[1]
    assert "role='alert'" in html and "Choose a status." in html


def test_select_field_escapes_labels_and_values(render):
    html = render(
        "{{ select_field('Team', 'team', 'team', options) }}",
        options=[("x' onmouseover='alert(1)", "<script>alert(1)</script>")],
    )
    assert "<script>alert" not in html
    assert "onmouseover='alert" not in html
    (option,) = elements(html, "option")
    assert option["value"] == "x' onmouseover='alert(1)"


def test_audit_filters_use_the_select_and_still_filter_without_javascript(dataset_browser):
    _factory, _services, admin, client = dataset_browser
    login(client)
    page = request(client, "get", "/settings/audit-log")
    assert page.status_code == 200
    selects = {attrs["id"]: attrs for attrs in elements(body(page), "select")}
    assert {"audit-actor", "audit-action", "audit-type"} <= selects.keys()
    assert all(
        "data-select" in selects[key] for key in ("audit-actor", "audit-action", "audit-type")
    )
    assert selects["audit-actor"]["aria-describedby"] == "audit-actor-help"
    assert selects["audit-actor"]["data-noun"] == "actors"

    filtered = request(client, "get", "/settings/audit-log", query_string={"actor": str(admin.id)})
    assert filtered.status_code == 200
    actor_options = [
        attrs for attrs in elements(body(filtered), "option") if attrs.get("value") == str(admin.id)
    ]
    assert actor_options and "selected" in actor_options[0]


def test_select_script_is_loaded_on_every_page(dataset_browser):
    _factory, _services, _admin, client = dataset_browser
    login(client)
    page = body(request(client, "get", "/"))
    scripts = [attrs.get("src", "") for attrs in elements(page, "script")]
    assert any(src.endswith("/static/js/select.js") for src in scripts)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
def test_select_script_is_valid_javascript():
    result = subprocess.run(
        ["node", "--check", str(ROOT / "src/refrncr/static/js/select.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

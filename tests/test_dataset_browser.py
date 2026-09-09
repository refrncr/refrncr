import re
from decimal import Decimal
from pathlib import Path

import pytest
from flask import template_rendered
from jinja2 import BaseLoader
from sqlalchemy import func, select, update
from sqlalchemy.pool import StaticPool

from refrncr.app import create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, Dataset, DatasetField, Grant
from refrncr.persistence import SqlAlchemyUnitOfWork
from refrncr.services import NotFoundError, ResourceTypeRegistry
from refrncr.web import SESSION_COOKIE, create_flask_app

HTTPS = "https://localhost"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def dataset_browser():
    engine = create_database_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Administrator", PASSWORD)
    client = create_flask_app(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        authorization=services.authorization,
        datasets=services.datasets,
        dataset_fields=services.dataset_fields,
        dataset_business_keys=services.dataset_business_keys,
    ).test_client()
    return factory, services, admin, client


def request(client, method, path, **kwargs):
    return getattr(client, method)(path, base_url=HTTPS, **kwargs)


def body(response):
    return response.get_data(as_text=True)


def form_csrf(response):
    match = re.search(r"name='csrf_token' value='([^']+)'", body(response))
    assert match is not None
    return match.group(1)


def login(client, email="admin@example.com", password=PASSWORD):
    login_form = request(client, "get", "/login")
    response = request(
        client,
        "post",
        "/login",
        data={
            "email": email,
            "password": password,
            "csrf_token": form_csrf(login_form),
        },
    )
    assert response.status_code == 302


def session_csrf(client, services):
    cookie = client.get_cookie(SESSION_COOKIE)
    assert cookie is not None
    resolved = services.sessions.resolve_with_session(cookie.value)
    assert resolved is not None
    return resolved[1].csrf_token


def create_dataset(services, admin, key, name=None, description=""):
    return services.datasets.create_dataset(
        admin.id,
        key,
        name or key.replace("-", " ").title(),
        description,
    )


def create_field(
    services,
    actor,
    dataset,
    key="field",
    name="Field",
    data_type="string",
    is_required=False,
    is_unique=False,
):
    return services.dataset_fields.create_field(
        actor.id,
        dataset.key,
        key,
        name,
        data_type,
        is_required,
        is_unique,
    )


@pytest.mark.parametrize(
    ("method", "path", "data"),
    [
        ("get", "/datasets", None),
        ("get", "/datasets/new", None),
        ("post", "/datasets", {"key": "new", "name": "New", "description": ""}),
        ("get", "/datasets/example", None),
        ("get", "/datasets/example/edit", None),
        ("post", "/datasets/example", {"name": "Changed", "description": ""}),
        ("get", "/datasets/example/fields/new", None),
        ("post", "/datasets/example/fields", {"key": "field", "name": "Field"}),
        ("get", "/datasets/example/fields/field/edit", None),
        ("post", "/datasets/example/fields/field", {"name": "Changed"}),
        ("post", "/datasets/example/fields/field/delete", None),
    ],
)
def test_all_dataset_routes_require_authentication(dataset_browser, method, path, data):
    _, _, _, client = dataset_browser

    response = request(client, method, path, data=data)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?")


def test_dataset_navigation_and_empty_list_state(dataset_browser):
    _, _, _, client = dataset_browser
    login(client)

    assert "Datasets" in body(request(client, "get", "/"))
    response = request(client, "get", "/datasets")

    assert response.status_code == 200
    assert "No datasets are available." in body(response)
    assert "New dataset" in body(response)
    assert "<h1>Datasets</h1>" in body(response)


def test_dataset_list_uses_file_template_context_and_accessible_rows(dataset_browser):
    _, services, admin, client = dataset_browser
    create_dataset(
        services,
        admin,
        "searchable",
        "Name <unsafe>",
        "Description 'unsafe' <markup>",
    )
    login(client)
    rendered_templates = []

    def record_template(_sender, template, **_extra):
        rendered_templates.append(template.name)

    template_rendered.connect(record_template, client.application)
    try:
        response = request(client, "get", "/datasets")
    finally:
        template_rendered.disconnect(record_template, client.application)
    page = body(response)

    assert response.status_code == 200
    assert rendered_templates == ["pages/datasets/list.html"]
    assert "<title>Datasets · REFRNCR</title>" in page
    assert "<h1>Datasets</h1>" in page
    assert "My Datasets" not in page
    assert "Search datasets" not in page
    assert "data-list-search" not in page
    assert "dataset-tabs" not in page
    assert "button-tab" not in page
    assert "<table aria-label='Datasets'>" in page
    assert "<th scope='col'>Dataset</th>" in page
    assert "<th scope='col' class='numeric-column'>Records</th>" in page
    assert "<th scope='col'>Updated</th>" in page
    row = re.search(r"<tr class='dataset-row interactive-row'([^>]*)>(.*?)</tr>", page, re.DOTALL)
    assert row is not None
    assert "tabindex" not in row.group(1)
    assert "role=" not in row.group(1)
    assert "data-row-action" in row.group(1)
    assert "data-list-row" not in row.group(1)
    assert "data-search-text" not in row.group(1)
    assert "data-row-primary href='/datasets/searchable/records'" in row.group(2)
    assert "<span class='row-action-label'>Name &lt;unsafe&gt;</span>" in row.group(2)
    assert "<td class='numeric-column'>0</td>" in row.group(2)
    assert "Name <unsafe>" not in page
    assert "<markup>" not in page
    assert "+ New dataset" in page


def test_shared_shell_marks_active_navigation_and_exposes_account_context(dataset_browser):
    _, _, _, client = dataset_browser
    login(client)

    response = request(client, "get", "/datasets")
    page = body(response)

    assert '<html lang="en">' in page
    assert '<main id="content"' in page
    assert 'aria-label="Primary navigation"' in page
    assert 'href="/datasets" aria-current="page"' in page
    assert "Administrator" in page
    assert "Administrator" in page
    assert 'action="/logout"' in page
    assert 'name="csrf_token"' in page
    assert "data-responsive-shell" in page


@pytest.mark.parametrize("path", ["/", "/datasets", "/users", "/teams"])
def test_shared_navigation_controls_and_local_bootstrap(dataset_browser, path):
    _, _, _, client = dataset_browser
    login(client)
    page = body(request(client, "get", path))
    target = "settings-sidebar" if path in ("/users", "/teams") else "application-navigation"
    for expanded, action, icon, arrow in (
        ("true", "Collapse", "close", "m16 15-3-3 3-3"),
        ("false", "Expand", "open", "m14 9 3 3-3 3"),
    ):
        button = re.search(
            rf'<button[^>]*aria-label="{action} navigation".*?</button>', page, re.S
        ).group()
        assert 'type="button"' in button
        assert f'title="{action} navigation"' in button
        assert f'aria-expanded="{expanded}"' in button
        assert f'aria-controls="{target}"' in button
        assert f'data-icon="panel-left-{icon}"' in button
        assert f'<path d="{arrow}"/>' in button
        assert 'aria-hidden="true" focusable="false"' in button
    assert f'id="{target}"' in page
    assert '<dialog class="navigation-drawer" aria-label="Navigation"' in page
    script = '<script src="/static/js/navigation.js"></script>'
    assert script in page
    assert page.index(script) < page.index('<link rel="stylesheet"')


def test_shared_shell_navigation_follows_effective_role(dataset_browser):
    _, services, admin, client = dataset_browser
    viewer = services.users.create_user(
        admin.id, "shell-viewer@example.com", "Shell Viewer", "viewer password"
    )
    dataset = create_dataset(services, admin, "shell-visible")
    services.grants.create_grant(admin.id, "user", viewer.id, "dataset", dataset.id, "viewer")
    login(client, viewer.email, "viewer password")

    page = body(request(client, "get", "/datasets"))

    assert 'href="/datasets"' in page
    assert 'href="/users"' not in page
    assert 'href="/teams"' not in page
    assert "New dataset" not in page


@pytest.mark.parametrize("permission", ["viewer", "editor", "admin"])
def test_resource_permissions_show_dataset_without_community_admin_create_action(
    dataset_browser, permission
):
    _, services, admin, client = dataset_browser
    user = services.users.create_user(
        admin.id,
        f"resource-{permission}@example.com",
        f"Resource {permission.title()}",
        "user password",
    )
    dataset = create_dataset(services, admin, f"{permission}-visible")
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, permission)
    login(client, user.email, "user password")

    page = body(request(client, "get", "/datasets"))

    assert f"href='/datasets/{dataset.key}/records'" in page
    assert "+ New dataset" not in page


def test_ui_assets_are_local_and_critical_login_controls_are_labelled(dataset_browser):
    _, _, _, client = dataset_browser

    page = body(request(client, "get", "/login"))

    assert "fonts.googleapis.com" not in page
    assert "cdn.jsdelivr.net" not in page
    assert "for='login-email'" in page
    assert "for='login-password'" in page
    assert "autocomplete='current-password'" in page
    assert "class='login-form'" in page
    assert '<main id="content"' in page


def test_design_tokens_and_responsive_hooks_are_served_locally(dataset_browser):
    _, _, _, client = dataset_browser

    response = request(client, "get", "/static/css/refrncr.css")
    entrypoint = body(response)
    tokens = body(request(client, "get", "/static/css/tokens.css"))
    app_shell = body(request(client, "get", "/static/css/layouts/app-shell.css"))
    auth = body(request(client, "get", "/static/css/layouts/auth.css"))

    assert response.status_code == 200
    assert entrypoint.splitlines() == [
        '@import url("tokens.css");',
        '@import url("base.css");',
        '@import url("utilities.css");',
        '@import url("layouts/app-shell.css");',
        '@import url("layouts/page.css");',
        '@import url("layouts/configuration.css");',
        '@import url("layouts/settings.css");',
        '@import url("layouts/auth.css");',
        '@import url("layouts/import.css");',
        '@import url("components/buttons.css");',
        '@import url("components/forms.css");',
        '@import url("components/feedback.css");',
        '@import url("components/cards-badges.css");',
        '@import url("components/tables.css");',
        '@import url("components/dialog.css");',
    ]
    assert "--color-action-primary:" in tokens
    assert "--space-md:" in tokens
    assert "--radius-lg:" in tokens
    assert "--shadow-md:" in tokens
    assert "--breakpoint-tablet: 768px" in tokens
    assert "@media (max-width: 767px)" in app_shell
    assert '.login-form button[type="submit"]' in auth
    assert "https://" not in entrypoint + tokens + app_shell + auth

    script = body(request(client, "get", "/static/js/refrncr.js"))
    assert 'document.querySelectorAll(".export-menu")' in script
    assert 'menu.removeAttribute("open")' in script


def test_record_overview_renders_export_import_and_add_as_buttons(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "button-actions")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    create_field(services, admin, dataset, "active", "Actief", data_type="boolean")
    login(client)

    page = body(request(client, "get", f"/datasets/{dataset.key}/records"))

    assert "<div class='page-actions record-page-actions'>" in page
    assert "images/lucide-download.svg" in page
    assert "images/lucide-settings-2.svg" in page
    assert "images/lucide-upload.svg" in page
    assert "images/lucide-plus.svg" in page
    assert "Export</summary>" in page
    assert "format=csv" in page and "format=xlsx" in page and "format=json" in page
    assert "class='button button-secondary'" in page and "Import</a>" in page
    assert "href='/datasets/button-actions'>" in page and "Configure</a>" in page
    assert "data-dialog-open='add-record-panel'" in page
    assert "<dialog class='side-panel' id='add-record-panel' data-side-panel" in page
    assert "New button-action record" in page
    assert "class='button button-primary' type='submit'>Create record</button>" in page
    assert "action='/datasets/button-actions/records'" in page
    assert "name='csrf_token'" in page
    assert "for='new-record-code'" in page
    assert "id='new-record-code' name='code' type='text' required autofocus" in page
    assert "id='new-record-active' name='active'" in page
    assert "Search records" not in page
    assert ">Filter</button>" not in page and ">Sort</button>" not in page
    assert "Showing 0–0 of 0" in page


def test_record_rows_open_a_detail_panel_with_record_actions(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "currencies")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    record = services.dataset_records.create_record(admin.id, dataset.key, {"code": "EUR"})
    login(client)

    page = body(request(client, "get", f"/datasets/{dataset.key}/records"))

    assert f"data-dialog-open='record-panel-{record.id}'" in page
    assert "<caption class='visually-hidden'>Records</caption>" in page
    assert "data-record-row" in page
    assert "<tr class='record-row interactive-row' data-row-action data-record-row" in page
    assert not re.search(r"<tr\b[^>]*(?:tabindex=|role=['\"]button)", page)
    assert f"type='button' data-dialog-open='record-panel-{record.id}'" in page
    assert f"aria-label='View record EUR ({record.id})'" in page
    assert ">View</button>" not in page
    assert f"id='record-panel-{record.id}'" in page
    assert ">EUR</h2>" in page
    assert f"aria-labelledby='record-panel-title-{record.id}'" in page
    assert f"aria-describedby='record-panel-description-{record.id}'" in page
    assert "data-panel-subtitle data-default-text='View record'>View record</p>" in page
    assert f"action='/datasets/currencies/records/{record.id}/edit'" in page
    assert f"action='/datasets/currencies/records/{record.id}/delete'" in page
    assert "data-panel-mode='edit'" in page
    assert "data-panel-view='edit' hidden" in page
    assert "value='1'" in page
    assert "This deletion cannot be undone." in page
    assert "name='confirm' value='yes'" in page


def test_record_table_right_aligns_and_pads_number_columns(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "measurements")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    create_field(services, admin, dataset, "amount", "Amount", data_type="number")
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    one = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "one", "amount": "1.2"}
    )
    three = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "two", "amount": "3.456"}
    )
    zero = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "zero", "amount": "0.00"}
    )
    negative = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "negative", "amount": "-2.5"}
    )
    login(client)

    page = body(request(client, "get", f"/datasets/{dataset.key}/records"))

    assert re.search(r"<th scope='col' class='numeric-column'>\s*Amount", page)
    for value in ("1.200", "3.456", "0.000", "-2.500"):
        assert re.search(rf"<td class='numeric-column'>\s*{re.escape(value)}\s*</td>", page)
    persisted = {
        record.id: dict((value.field_key, value.value) for value in record.values)["amount"]
        for record in services.dataset_records.list_records(admin.id, dataset.key).records
    }
    assert persisted == {
        one.id: Decimal("1.2"),
        three.id: Decimal("3.456"),
        zero.id: Decimal("0.00"),
        negative.id: Decimal("-2.5"),
    }


def test_record_table_displays_hyphen_for_empty_cells(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "optional-values")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    create_field(services, admin, dataset, "note", "Note")
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    services.dataset_records.create_record(admin.id, dataset.key, {"code": "one"})
    login(client)

    page = body(request(client, "get", f"/datasets/{dataset.key}/records"))

    assert "<span aria-hidden='true'>-</span><span class='visually-hidden'>No value</span>" in page


def test_record_table_uses_monospace_data_and_48_pixel_rows(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "table-style")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    services.dataset_records.create_record(admin.id, dataset.key, {"code": "ABC"})
    login(client)

    page = body(request(client, "get", f"/datasets/{dataset.key}/records"))
    stylesheet = body(request(client, "get", "/static/css/components/tables.css"))

    assert "<table class='record-table'>" in page
    assert "min-height: var(--record-row-min-height)" in stylesheet
    assert "height: var(--record-row-min-height)" in stylesheet
    assert ".record-table td" in stylesheet
    assert (
        "color: var(--color-text-data); font-family: var(--font-data); "
        "font-size: var(--font-size-xs)" in stylesheet
    )


def test_record_table_retains_readable_width_inside_scroll_container(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "wide-records")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    create_field(services, admin, dataset, "description", "Description")
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    services.dataset_records.create_record(
        admin.id,
        dataset.key,
        {"code": "EUR", "description": "Euro"},
    )
    login(client)

    page = body(request(client, "get", f"/datasets/{dataset.key}/records"))
    tokens = body(request(client, "get", "/static/css/tokens.css"))
    tables = body(request(client, "get", "/static/css/components/tables.css"))

    assert re.search(r"<div class='table-scroll'>\s*<table class='record-table'>", page)
    assert "--data-table-min-width: 48rem" in tokens
    assert (
        ".record-table { width: max(100%, var(--data-table-min-width)); table-layout: fixed; }"
        in tables
    )
    assert ".table-scroll { width: 100%; overflow-x: auto;" in tables
    assert not re.search(r"\.record-table\s+th[^}]*white-space:\s*nowrap", tables)
    assert "currencies" not in tables.lower()


def test_record_table_truncates_text_with_full_value_tooltip(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "long-text")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    create_field(services, admin, dataset, "note", "Note")
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    long_text = "A long description that should be truncated in the record table"
    services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "LONG", "note": long_text}
    )
    login(client)

    page = body(request(client, "get", f"/datasets/{dataset.key}/records"))
    tables = body(request(client, "get", "/static/css/components/tables.css"))
    dialog = body(request(client, "get", "/static/css/components/dialog.css"))

    assert f"<span class='cell-content' title='{long_text}'>{long_text}</span>" in page
    assert "table-layout: fixed" in tables
    assert "text-overflow: ellipsis" in tables
    assert ".side-panel-header h2" in dialog
    assert "font-family: var(--font-display)" in dialog


def test_all_design_system_assets_are_local_and_served(dataset_browser):
    _, _, _, client = dataset_browser
    css_files = (
        "base.css",
        "utilities.css",
        "layouts/app-shell.css",
        "layouts/page.css",
        "layouts/auth.css",
        "components/buttons.css",
        "components/forms.css",
        "components/feedback.css",
        "components/cards-badges.css",
        "components/tables.css",
        "components/dialog.css",
    )
    fonts = (
        "montserrat/Montserrat-Medium.woff2",
        "inter/Inter-Regular.woff2",
        "inter/Inter-SemiBold.woff2",
        "inter/Inter-Bold.woff2",
        "jetbrains-mono/JetBrainsMono-Regular.woff2",
    )
    for filename in css_files:
        assert request(client, "get", f"/static/css/{filename}").status_code == 200
    for filename in fonts:
        response = request(client, "get", f"/static/fonts/{filename}")
        assert response.status_code == 200
        assert response.mimetype == "font/woff2"
    tokens = body(request(client, "get", "/static/css/tokens.css"))
    assert tokens.count("@font-face") == 5
    assert "Montserrat-Medium.woff2" in tokens and "font-weight: 500" in tokens
    assert "Inter-Regular.woff2" in tokens and "Inter-SemiBold.woff2" in tokens
    assert "Inter-Bold.woff2" in tokens and "JetBrainsMono-Regular.woff2" in tokens
    assert "http://" not in tokens and "https://" not in tokens


def test_component_css_uses_explicit_contract_selectors(dataset_browser):
    _, _, _, client = dataset_browser
    buttons = body(request(client, "get", "/static/css/components/buttons.css"))
    forms = body(request(client, "get", "/static/css/components/forms.css"))
    tables = body(request(client, "get", "/static/css/components/tables.css"))
    assert "button:not([class])" not in buttons
    assert ".button {" in buttons
    assert "\nform {" not in forms
    assert ".form {" in forms
    assert "\ntbody tr:hover" not in tables
    assert ".table-scroll tbody tr:not(.interactive-row):hover" in tables


def test_local_lucide_set_has_only_template_consumers_and_packaged_notices(dataset_browser):
    _, _, _, client = dataset_browser
    repository = Path(__file__).resolve().parents[1]
    image_directory = repository / "src/refrncr/static/images"
    template_directory = repository / "src/refrncr/templates"
    lucide_files = {path.name for path in image_directory.glob("lucide-*.svg")}
    template_source = "".join(path.read_text() for path in template_directory.rglob("*.html"))

    assert lucide_files == {
        "lucide-download.svg",
        "lucide-key-round.svg",
        "lucide-plus.svg",
        "lucide-settings-2.svg",
        "lucide-upload.svg",
    }
    for filename in lucide_files:
        assert filename in template_source
    for filename in ("LICENSE-Lucide.txt", "ASSET-SOURCES.md"):
        assert request(client, "get", f"/static/licenses/{filename}").status_code == 200


def test_record_table_include_and_view_button_pointer_guard(dataset_browser, monkeypatch):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "include")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    services.dataset_records.create_record(admin.id, dataset.key, {"code": "VALUE"})
    login(client)
    loaded = []
    loader = client.application.jinja_env.loader
    original_get_source = loader.get_source

    def record_source(environment, template):
        loaded.append(template)
        return original_get_source(environment, template)

    monkeypatch.setattr(loader, "get_source", record_source)

    response = request(client, "get", f"/datasets/{dataset.key}/records")
    script = body(request(client, "get", "/static/js/refrncr.js"))

    assert response.status_code == 200
    assert "pages/datasets/records.html" in loaded
    assert "pages/datasets/_record_table.html" in loaded
    assert "pages/datasets/_record_panels.html" in loaded
    assert "pages/datasets/_record_form_fields.html" in loaded
    assert "components/side_panel.html" in loaded
    assert "const rowInteractiveSelector" in script
    assert 'document.querySelectorAll("[data-record-row]").forEach' not in script


def test_record_side_panels_use_the_generic_modal_focus_contract(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "modal-contract")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    services.dataset_records.create_record(admin.id, dataset.key, {"code": "FOCUS"})
    login(client)

    page = body(request(client, "get", f"/datasets/{dataset.key}/records"))
    script = body(request(client, "get", "/static/js/refrncr.js"))

    assert "data-side-panel data-modal-side-panel" in page
    assert "autofocus data-initial-focus" in page
    assert "dialog.showModal();" in script
    assert "sidePanelOpeners.set(dialog, trigger);" in script
    assert "const opener = sidePanelOpeners.get(dialog);" in script
    assert "opener?.isConnected" in script
    assert 'document.getElementById("content")?.focus();' in script
    assert 'dialog.addEventListener("close"' in script
    # Explicit Tab wrapping belongs only to the new error-report variant; side panels
    # retain their native keyboard behavior.
    report_controller = script.split('document.querySelectorAll("dialog[data-modal-report]")')[
        1
    ].split('document.querySelectorAll("[data-dialog-open]")')[0]
    assert script.count('dialog.addEventListener("keydown"') == 1
    assert 'dialog.addEventListener("keydown"' in report_controller
    assert "innerHTML" not in script


def test_record_page_has_no_inline_markup_and_side_panel_component_is_generic():
    repository = Path(__file__).parents[1]
    web_source = (repository / "src/refrncr/web.py").read_text()
    record_route = web_source.split("def dataset_records_view", 1)[1].split(
        "def export_dataset_records", 1
    )[0]
    side_panel = (repository / "src/refrncr/templates/components/side_panel.html").read_text()

    assert "return render_template(" in record_route
    assert '"pages/datasets/records.html"' in record_route
    assert "render_template_string" not in record_route
    assert "<dialog" not in record_route and "<form" not in record_route
    assert "record" not in side_panel
    assert "permission" not in side_panel and "csrf" not in side_panel
    assert "|safe" not in side_panel


def test_web_module_has_no_legacy_string_templates():
    web_source = (Path(__file__).parents[1] / "src/refrncr/web.py").read_text()

    assert "render_template_string" not in web_source
    assert "flask_render_template_string" not in web_source
    assert '"<form' not in web_source and '"<dialog' not in web_source
    assert '"{% ' not in web_source and '"{{ ' not in web_source


def test_record_table_pagination_keeps_range_and_links(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "pages")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    for index in range(51):
        services.dataset_records.create_record(admin.id, dataset.key, {"code": f"VALUE-{index:02}"})
    login(client)

    first = body(request(client, "get", f"/datasets/{dataset.key}/records"))
    second = body(request(client, "get", f"/datasets/{dataset.key}/records?page=2"))

    assert "Showing 1–50 of 51" in first
    assert "href='/datasets/pages/records?page=2'>Next</a>" in first
    assert ">Previous</a>" not in first
    assert "Showing 51–51 of 51" in second
    assert "href='/datasets/pages/records?page=1'>Previous</a>" in second
    assert ">Next</a>" not in second


def test_viewer_keeps_record_view_button_without_mutation_actions(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "viewer-records")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    visible_record = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "VISIBLE"}
    )
    viewer = services.users.create_user(
        admin.id, "record-viewer@example.com", "Record viewer", "viewer password"
    )
    services.grants.create_grant(admin.id, "user", viewer.id, "dataset", dataset.id, "viewer")
    login(client, viewer.email, "viewer password")

    page = body(request(client, "get", f"/datasets/{dataset.key}/records"))

    assert f"aria-label='View record VISIBLE ({visible_record.id})'" in page
    assert "Export</summary>" in page
    assert "Configure</a>" not in page
    assert "Import</a>" not in page
    assert "Add record</button>" not in page
    assert "data-panel-mode='edit'" not in page
    assert "data-panel-mode='delete'" not in page


def test_list_uses_direct_and_team_grants_and_ignores_ownership(dataset_browser):
    factory, services, admin, client = dataset_browser
    user = services.users.create_user(admin.id, "viewer@example.com", "Viewer", "viewer password")
    direct = create_dataset(services, admin, "alpha-direct")
    team_dataset = create_dataset(services, admin, "beta-team")
    hidden = create_dataset(services, admin, "hidden-owned")
    services.grants.create_grant(admin.id, "user", user.id, "dataset", direct.id, "viewer")
    team = services.teams.create_team(admin.id, "Dataset viewers")
    services.memberships.create_membership(admin.id, user.id, team.id)
    services.grants.create_grant(admin.id, "team", team.id, "dataset", team_dataset.id, "viewer")
    with factory.begin() as session:
        session.execute(
            update(Dataset).where(Dataset.id == hidden.id).values(owner_user_id=user.id)
        )
    login(client, "viewer@example.com", "viewer password")

    response = request(client, "get", "/datasets")
    page = body(response)

    assert response.status_code == 200
    assert page.index("alpha-direct") < page.index("beta-team")
    assert "href='/datasets/alpha-direct/records'" in page
    assert "href='/datasets/beta-team/records'" in page
    assert "hidden-owned" not in page
    assert "Create dataset" not in page
    assert "data-list-search" not in page
    assert "data-list-row" not in page
    assert "<th scope='col'>Dataset</th>" in page
    assert request(client, "get", "/datasets/beta-team").status_code == 200
    assert "Configure" not in body(request(client, "get", "/datasets/beta-team/records"))


def test_admin_create_flow_redirects_and_persists_grant_and_audits(dataset_browser):
    factory, services, admin, client = dataset_browser
    login(client)
    create_form = request(client, "get", "/datasets/new")

    response = request(
        client,
        "post",
        "/datasets",
        data={
            "key": "reference-data",
            "name": "  Reference data  ",
            "description": "  Preserved description  ",
            "csrf_token": form_csrf(create_form),
            "owner_user_id": "attacker-controlled",
            "id": "attacker-controlled",
            "updated_at": "1900-01-01",
            "permission": "viewer",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/datasets/reference-data")
    assert "reference-data" in body(request(client, "get", "/datasets"))
    with factory() as session:
        dataset = session.scalar(select(Dataset).where(Dataset._key == "reference-data"))
        grant = session.scalar(
            select(Grant).where(Grant.resource_type == "dataset", Grant.resource_id == dataset.id)
        )
        events = set(
            session.execute(
                select(AuditEvent.object_type, AuditEvent.action).where(
                    (AuditEvent.object_id == dataset.id) | (AuditEvent.object_id == grant.id)
                )
            ).tuples()
        )
    assert dataset.name == "Reference data"
    assert dataset.description == "  Preserved description  "
    assert dataset.owner_user_id == admin.id
    assert dataset.id != "attacker-controlled"
    assert grant.permission == "admin"
    assert events == {("dataset", "created"), ("grant", "created")}


def test_dataset_create_form_uses_shared_layout_fields_feedback_and_actions(dataset_browser):
    _, _, _, client = dataset_browser
    login(client)

    response = request(client, "get", "/datasets/new")
    page = body(response)

    assert response.status_code == 200
    assert "<title>New dataset · REFRNCR</title>" in page
    assert '<header class="page-header">' in page
    assert "<form class='form' novalidate method='post' action='/datasets'>" in page
    assert "<label for='dataset-key'>Key</label>" in page
    assert "id='dataset-key' name='key' type='text' value='' required" in page
    assert "aria-describedby='dataset-key-help'" in page
    assert "id='dataset-key-help'>A permanent technical identifier.</p>" in page
    assert "<label for='dataset-name'>Name</label>" in page
    assert "id='dataset-name' name='name' type='text' value='' required" in page
    assert "<label for='dataset-description'>Description</label>" in page
    assert "<textarea id='dataset-description' name='description'></textarea>" in page
    assert (
        "<a class='button button-secondary' data-panel-cancel href='/datasets'>Cancel</a>" in page
    )
    assert '<button class="button button-primary" type="submit">Create dataset</button>' in page
    assert "aria-invalid='true'" not in page


@pytest.mark.parametrize(
    ("overrides", "status", "error_field", "message"),
    [
        (
            {"key": "INVALID"},
            400,
            "dataset-key",
            "Dataset key must contain 1-100 lowercase ASCII letters",
        ),
        ({"name": "   "}, 400, "dataset-name", "Dataset name must contain"),
        (
            {"description": "x" * 10_001},
            400,
            "dataset-description",
            "Dataset description must not exceed 10000 characters",
        ),
    ],
)
def test_create_validation_safely_redisplays_values(
    dataset_browser, overrides, status, error_field, message
):
    factory, _, _, client = dataset_browser
    login(client)
    form = request(client, "get", "/datasets/new")
    submitted_markup = "<script>alert(1)</script>"
    data = {
        "key": "valid-key",
        "name": submitted_markup,
        "description": "Description",
        "csrf_token": form_csrf(form),
        **overrides,
    }

    response = request(client, "post", "/datasets", data=data)

    assert response.status_code == status
    page = body(response)
    assert submitted_markup not in page
    assert message in page
    assert page.count("aria-invalid='true'") == 1
    assert f"id='{error_field}-error'" in page
    if overrides.get("name") != "   ":
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    with factory() as session:
        assert session.scalar(select(func.count(Dataset.id))) == 0


def test_duplicate_key_is_safe_conflict_and_preserves_form(dataset_browser):
    _, services, admin, client = dataset_browser
    create_dataset(services, admin, "duplicate")
    login(client)
    form = request(client, "get", "/datasets/new")

    response = request(
        client,
        "post",
        "/datasets",
        data={
            "key": "duplicate",
            "name": "Duplicate",
            "description": "Preserved",
            "csrf_token": form_csrf(form),
        },
    )

    assert response.status_code == 409
    assert "Dataset key already exists in this installation" in body(response)
    assert "Preserved" in body(response)
    assert "id='dataset-key-error'" in body(response)
    assert body(response).count("aria-invalid='true'") == 1
    assert "sql" not in body(response).casefold()


@pytest.mark.parametrize("token", [None, "invalid", "é"])
def test_create_requires_valid_csrf(dataset_browser, token):
    factory, _, _, client = dataset_browser
    login(client)
    data = {"key": "blocked", "name": "Blocked", "description": ""}
    if token is not None:
        data["csrf_token"] = token

    response = request(client, "post", "/datasets", data=data)

    assert response.status_code == 403
    with factory() as session:
        assert session.scalar(select(func.count(Dataset.id))) == 0


def test_non_admin_cannot_use_create_flow(dataset_browser):
    factory, services, admin, client = dataset_browser
    services.users.create_user(admin.id, "user@example.com", "User", "user password")
    login(client, "user@example.com", "user password")

    assert request(client, "get", "/datasets/new").status_code == 403
    response = request(
        client,
        "post",
        "/datasets",
        data={
            "key": "forbidden",
            "name": "Forbidden",
            "description": "",
            "csrf_token": session_csrf(client, services),
        },
    )
    assert response.status_code == 403
    with factory() as session:
        assert session.scalar(select(func.count(Dataset.id))) == 0


def test_detail_authorization_owner_display_edit_link_and_escaping(dataset_browser):
    factory, services, admin, client = dataset_browser
    viewer = services.users.create_user(
        admin.id,
        "viewer@example.com",
        "<img src=x onerror=alert(1)>",
        "viewer password",
    )
    editor = services.users.create_user(admin.id, "editor@example.com", "Editor", "editor password")
    dataset = create_dataset(
        services,
        admin,
        "unsafe",
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(2)>",
    )
    with factory.begin() as session:
        session.execute(
            update(Dataset).where(Dataset.id == dataset.id).values(owner_user_id=viewer.id)
        )
    services.grants.create_grant(admin.id, "user", viewer.id, "dataset", dataset.id, "viewer")
    services.grants.create_grant(admin.id, "user", editor.id, "dataset", dataset.id, "editor")

    login(client, "viewer@example.com", "viewer password")
    viewer_page = body(request(client, "get", "/datasets/unsafe"))
    assert "<script>alert(1)</script>" not in viewer_page
    assert "<img src=x onerror=alert(2)>" not in viewer_page
    assert "<img src=x onerror=alert(1)>" not in viewer_page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in viewer_page
    assert "&lt;img src=x onerror=alert(2)&gt;" in viewer_page
    assert "&lt;img src=x onerror=alert(1)&gt;" in viewer_page
    assert "Edit dataset" not in viewer_page

    login(client, "editor@example.com", "editor password")
    assert "Edit dataset" in body(request(client, "get", "/datasets/unsafe"))


def test_missing_inaccessible_and_owner_only_detail_are_same_404(dataset_browser):
    factory, services, admin, client = dataset_browser
    user = services.users.create_user(admin.id, "user@example.com", "User", "user password")
    dataset = create_dataset(services, admin, "private")
    with factory.begin() as session:
        session.execute(
            update(Dataset).where(Dataset.id == dataset.id).values(owner_user_id=user.id)
        )
    login(client, "user@example.com", "user password")

    responses = [
        request(client, "get", "/datasets/private"),
        request(client, "get", "/datasets/missing"),
        request(client, "get", "/datasets/INVALID"),
    ]

    assert [response.status_code for response in responses] == [404, 404, 404]
    assert len({body(response) for response in responses}) == 1


@pytest.mark.parametrize("permission", ["editor", "admin"])
def test_granted_editor_or_admin_can_edit_with_prg_and_audit(dataset_browser, permission):
    factory, services, admin, client = dataset_browser
    user = services.users.create_user(admin.id, "editor@example.com", "Editor", "user password")
    dataset = create_dataset(services, admin, "editable", "Original", "Original description")
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, permission)
    login(client, "editor@example.com", "user password")
    edit_form = request(client, "get", "/datasets/editable/edit")
    assert edit_form.status_code == 200
    assert "name='key'" not in body(edit_form)
    csrf = form_csrf(edit_form)

    response = request(
        client,
        "post",
        "/datasets/editable",
        data={
            "name": "  Updated  ",
            "description": "  Preserved  ",
            "csrf_token": csrf,
            "key": "attacker-key",
            "owner_user_id": user.id,
            "id": "attacker-id",
            "created_at": "1900-01-01",
            "updated_at": "1900-01-01",
            "permission": "viewer",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/datasets/editable")
    with factory() as session:
        persisted = session.get(Dataset, dataset.id)
        audits = session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.object_id == dataset.id,
                AuditEvent.action == "metadata_updated",
            )
        )
    assert persisted.id == dataset.id
    assert persisted.key == "editable"
    assert persisted.owner_user_id == admin.id
    assert persisted.created_at.year != 1900
    assert persisted.updated_at.year != 1900
    assert persisted.name == "Updated"
    assert persisted.description == "  Preserved  "
    assert audits == 1


def test_dataset_edit_form_uses_shared_layout_fields_feedback_and_actions(dataset_browser):
    _, services, admin, client = dataset_browser
    create_dataset(
        services,
        admin,
        "edit-form",
        "Stored <name>",
        "Stored <description>",
    )
    login(client)

    response = request(client, "get", "/datasets/edit-form/edit")
    page = body(response)

    assert response.status_code == 200
    assert "<title>Edit dataset · REFRNCR</title>" in page
    assert '<header class="page-header">' in page
    assert "<p><span class='form-label'>Key:</span> <code>edit-form</code></p>" in page
    assert "name='key'" not in page
    assert (
        "<form id='dataset-edit-form' class='form' novalidate "
        "method='post' action='/datasets/edit-form'>" in page
    )
    assert "<label for='dataset-name'>Name</label>" in page
    assert "value='Stored &lt;name&gt;'" in page
    assert "<label for='dataset-description'>Description</label>" in page
    assert ">Stored &lt;description&gt;</textarea>" in page
    assert "data-panel-cancel" not in page
    assert "side-panel-footer-single" in page
    assert "type='submit' form='dataset-edit-form'>Save dataset</button>" in page
    assert "aria-invalid='true'" not in page
    assert "Stored <name>" not in page
    assert "Stored <description>" not in page


def test_community_admin_can_edit_without_dataset_grant(dataset_browser):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "admin-edit")
    with factory.begin() as session:
        session.execute(Grant.__table__.delete().where(Grant.resource_type == "dataset"))
    login(client)

    edit_form = request(client, "get", "/datasets/admin-edit/edit")
    response = request(
        client,
        "post",
        "/datasets/admin-edit",
        data={
            "name": "Admin updated",
            "description": "",
            "csrf_token": form_csrf(edit_form),
        },
    )

    assert edit_form.status_code == 200
    assert response.status_code == 302
    assert services.datasets.get_dataset(admin.id, dataset.key).name == "Admin updated"


def test_viewer_owner_and_missing_edit_paths_are_uniform_404_without_audit(dataset_browser):
    factory, services, admin, client = dataset_browser
    viewer = services.users.create_user(admin.id, "viewer@example.com", "Viewer", "user password")
    dataset = create_dataset(services, admin, "readonly")
    owned = create_dataset(services, admin, "owned-only")
    services.grants.create_grant(admin.id, "user", viewer.id, "dataset", dataset.id, "viewer")
    with factory.begin() as session:
        session.execute(
            update(Dataset).where(Dataset.id == owned.id).values(owner_user_id=viewer.id)
        )
    login(client, "viewer@example.com", "user password")
    csrf = session_csrf(client, services)

    responses = [
        request(client, "get", "/datasets/readonly/edit"),
        request(client, "get", "/datasets/owned-only/edit"),
        request(client, "get", "/datasets/missing/edit"),
        request(
            client,
            "post",
            "/datasets/readonly",
            data={"name": "Changed", "description": "", "csrf_token": csrf},
        ),
        request(
            client,
            "post",
            "/datasets/owned-only",
            data={"name": "Changed", "description": "", "csrf_token": csrf},
        ),
        request(
            client,
            "post",
            "/datasets/missing",
            data={"name": "Changed", "description": "", "csrf_token": csrf},
        ),
    ]

    assert [response.status_code for response in responses] == [404] * 6
    assert len({body(response) for response in responses}) == 1
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_id == dataset.id,
                    AuditEvent.action == "metadata_updated",
                )
            )
            == 0
        )


@pytest.mark.parametrize("token", [None, "invalid", "é"])
def test_edit_requires_valid_csrf(dataset_browser, token):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "csrf-edit", "Original")
    login(client)
    data = {"name": "Changed", "description": ""}
    if token is not None:
        data["csrf_token"] = token

    response = request(client, "post", "/datasets/csrf-edit", data=data)

    assert response.status_code == 403
    with factory() as session:
        assert session.get(Dataset, dataset.id).name == "Original"


def test_invalid_edit_redisplays_escaped_values_without_mutation(dataset_browser):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "invalid-edit", "Original", "Original")
    login(client)
    edit_form = request(client, "get", "/datasets/invalid-edit/edit")
    markup = "<script>alert(1)</script>"

    response = request(
        client,
        "post",
        "/datasets/invalid-edit",
        data={
            "name": markup,
            "description": "x" * 10_001,
            "csrf_token": form_csrf(edit_form),
        },
    )

    assert response.status_code == 400
    page = body(response)
    assert markup not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "Dataset description must not exceed 10000 characters" in page
    assert "id='dataset-edit-error'" in page
    assert "aria-describedby='dataset-edit-error' aria-invalid='true'" in page
    assert page.count("aria-invalid='true'") == 1
    with factory() as session:
        persisted = session.get(Dataset, dataset.id)
        assert persisted.name == "Original"
        assert persisted.description == "Original"


def test_dataset_detail_field_empty_state_order_values_and_admin_controls(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "schema")
    login(client)

    empty_page = body(request(client, "get", "/datasets/schema"))
    assert "<h2 id='fields-heading'>Fields</h2>" in empty_page
    assert "No fields are defined for this dataset." in empty_page
    assert "Add field" in empty_page

    create_field(services, admin, dataset, "zeta", "Zeta", "date", True, False)
    create_field(services, admin, dataset, "alpha", "<script>alert(1)</script>", "string")
    rendered_templates = []

    def record_template(_sender, template, **_extra):
        rendered_templates.append(template.name)

    template_rendered.connect(record_template, client.application)
    try:
        response = request(client, "get", "/datasets/schema")
    finally:
        template_rendered.disconnect(record_template, client.application)
    page = body(response)

    assert response.status_code == 200
    assert rendered_templates == ["pages/datasets/detail.html"]
    assert '<nav class="breadcrumbs" aria-label="Breadcrumb">' in page
    assert '<a href="/datasets">Datasets</a>' in page
    assert "<caption class='visually-hidden'>Dataset fields</caption>" in page
    assert page.index("zeta") < page.index("alpha")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "string" in page and "date" in page
    assert "Required" in page and "None" in page
    assert page.count("Edit field") == 2
    for name in ("Zeta", "&lt;script&gt;alert(1)&lt;/script&gt;"):
        for direction in ("up", "down"):
            assert f"aria-label='Move {name} {direction}'" in page
    order_forms = re.findall(
        r"<form class='button-stack' method='post' "
        r"action='/datasets/schema/fields/order'>(.*?)</form>",
        page,
        re.DOTALL,
    )
    assert len(order_forms) == 2
    expected_ids = [
        field.id for field in services.dataset_fields.list_fields(admin.id, dataset.key)
    ]
    csrf = session_csrf(client, services)
    for form, field_id in zip(order_forms, expected_ids, strict=True):
        assert form.count(f"name='csrf_token' value='{csrf}'") == 1
        assert re.findall(r"name='expected_field_id' value='([^']+)'", form) == expected_ids
        assert f"name='field_id' value='{field_id}'" in form
        assert "name='direction' value='up'" in form
        assert "name='direction' value='down'" in form
        controls = re.findall(r"<button([^>]*)>(.*?)</button>", form, re.DOTALL)
        assert len(controls) == 2
        for attributes, content in controls:
            assert "type='submit'" in attributes
            assert "button-icon-only" in attributes
            assert '<svg class="button-icon"' in content
            assert 'aria-hidden="true"' in content and 'focusable="false"' in content
            assert not re.sub(r"<[^>]+>", "", content).strip()
        assert '<path d="m6 15 6-6 6 6"' in controls[0][1]
        assert '<path d="m6 9 6 6 6-6"' in controls[1][1]
        assert not re.search(r"draggable|ondrag\w*|ondrop", form, re.IGNORECASE)
    assert "name='direction' value='up' disabled" in order_forms[0]
    assert "name='direction' value='down' disabled" not in order_forms[0]
    assert "name='direction' value='up' disabled" not in order_forms[1]
    assert "name='direction' value='down' disabled" in order_forms[1]


def test_dataset_detail_maps_late_field_not_found_to_ordinary_404(dataset_browser, monkeypatch):
    _, services, admin, client = dataset_browser
    create_dataset(services, admin, "revoked-during-read")
    login(client)
    monkeypatch.setattr(
        services.dataset_fields,
        "list_fields",
        lambda _actor_id, _dataset_key: (_ for _ in ()).throw(NotFoundError()),
    )

    response = request(client, "get", "/datasets/revoked-during-read")

    assert response.status_code == 404
    assert body(response) == body(request(client, "get", "/does-not-exist"))


@pytest.mark.parametrize("permission", ["viewer", "editor"])
def test_viewer_and_editor_see_fields_without_schema_controls(dataset_browser, permission):
    _, services, admin, client = dataset_browser
    user = services.users.create_user(
        admin.id, f"{permission}@example.com", permission.title(), "user password"
    )
    dataset = create_dataset(services, admin, "visible-schema")
    create_field(services, admin, dataset, "field", "Visible field", "boolean", True, True)
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, permission)
    login(client, f"{permission}@example.com", "user password")

    page = body(request(client, "get", "/datasets/visible-schema"))

    assert "Visible field" in page
    assert "Add field" not in page
    assert "Edit field" not in page
    assert "name='direction'" not in page


def test_community_admin_override_shows_schema_controls_without_grant(dataset_browser):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "override")
    field = create_field(services, admin, dataset)
    with factory.begin() as session:
        session.execute(Grant.__table__.delete().where(Grant.resource_type == "dataset"))
    login(client)

    page = body(request(client, "get", "/datasets/override"))

    assert "Add field" in page
    assert "Edit field" in page
    assert request(client, "get", f"/datasets/override/fields/{field.key}/edit").status_code == 200


@pytest.mark.parametrize("actor_kind", ["community-admin", "dataset-admin"])
@pytest.mark.parametrize(
    "direction, expected_keys",
    [
        ("up", ["second", "first", "third"]),
        ("down", ["first", "third", "second"]),
    ],
)
def test_admin_moves_field_and_stale_form_is_rejected_without_second_audit(
    dataset_browser, direction, expected_keys, actor_kind
):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "ordered")
    fields = [
        create_field(services, admin, dataset, key, key.title())
        for key in ("first", "second", "third")
    ]
    if actor_kind == "dataset-admin":
        actor = services.users.create_user(
            admin.id, "schema-admin@example.com", "Admin", "user password"
        )
        services.grants.create_grant(admin.id, "user", actor.id, "dataset", dataset.id, "admin")
        login(client, actor.email, "user password")
    else:
        login(client)
    expected = [field.id for field in fields]
    csrf = session_csrf(client, services)
    data = {
        "csrf_token": csrf,
        "expected_field_id": expected,
        "field_id": fields[1].id,
        "direction": direction,
    }

    moved = request(client, "post", "/datasets/ordered/fields/order", data=data)
    stale = request(client, "post", "/datasets/ordered/fields/order", data=data)

    assert moved.status_code == 302
    assert stale.status_code == 409
    assert moved.location == "/datasets/ordered"
    assert [
        field.key for field in services.dataset_fields.list_fields(admin.id, dataset.key)
    ] == expected_keys
    page = body(request(client, "get", moved.location))
    assert re.findall(r"<span class='field-name'>(.*?)</span>", page) == [
        key.title() for key in expected_keys
    ]
    with factory() as session:
        persisted = session.scalars(
            select(DatasetField)
            .where(DatasetField.dataset_id == dataset.id)
            .order_by(DatasetField.display_position)
        ).all()
        assert [field.key for field in persisted] == expected_keys
        assert [field.display_position for field in persisted] == [1, 2, 3]
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.action == "field_order_updated")
            )
            == 1
        )


def test_owner_without_permission_cannot_view_schema_or_controls(dataset_browser):
    factory, services, admin, client = dataset_browser
    owner = services.users.create_user(admin.id, "owner@example.com", "Owner", "owner password")
    dataset = create_dataset(services, admin, "owned-schema")
    create_field(services, admin, dataset)
    with factory.begin() as session:
        session.execute(
            update(Dataset).where(Dataset.id == dataset.id).values(owner_user_id=owner.id)
        )
    login(client, "owner@example.com", "owner password")

    response = request(client, "get", "/datasets/owned-schema")

    assert response.status_code == 404
    assert "Add field" not in body(response)
    assert "Edit field" not in body(response)


def test_admin_create_field_form_lists_exact_types(dataset_browser):
    _, services, admin, client = dataset_browser
    create_dataset(services, admin, "form")
    login(client)

    response = request(client, "get", "/datasets/form/fields/new")
    page = body(response)

    assert response.status_code == 200
    assert [
        choice for choice in ("string", "number", "boolean", "date") if f"value='{choice}'" in page
    ] == ["string", "number", "boolean", "date"]
    assert "name='key'" in page
    assert "name='is_required'" in page
    assert "name='is_unique'" in page


@pytest.mark.parametrize("data_type", ["string", "number", "boolean", "date"])
def test_admin_create_field_browser_flow_accepts_supported_types(dataset_browser, data_type):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "create-field")
    login(client)
    form = request(client, "get", "/datasets/create-field/fields/new")

    response = request(
        client,
        "post",
        "/datasets/create-field/fields",
        data={
            "key": data_type,
            "name": data_type.title(),
            "data_type": data_type,
            "csrf_token": form_csrf(form),
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/datasets/create-field")
    field = services.dataset_fields.get_field(admin.id, dataset.key, data_type)
    assert field.data_type == data_type
    assert field.is_required is False
    assert field.is_unique is False


def test_create_field_checkboxes_and_mass_assignment_are_explicit(dataset_browser):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "allowlist")
    login(client)
    form = request(client, "get", "/datasets/allowlist/fields/new")

    response = request(
        client,
        "post",
        "/datasets/allowlist/fields",
        data={
            "key": "protected",
            "name": "Protected",
            "data_type": "string",
            "is_required": "attacker-value",
            "is_unique": "attacker-value",
            "csrf_token": form_csrf(form),
            "id": "attacker-id",
            "dataset_id": "attacker-dataset",
            "created_at": "1900-01-01",
            "updated_at": "1900-01-01",
            "actor_user_id": "attacker-user",
            "object_type": "attacker-type",
            "owner_user_id": "attacker-owner",
            "permission": "viewer",
            "tenant_id": "attacker-tenant",
        },
    )

    assert response.status_code == 302
    with factory() as session:
        field = session.scalar(
            select(DatasetField).where(
                DatasetField.dataset_id == dataset.id,
                DatasetField._key == "protected",
            )
        )
        persisted_dataset = session.get(Dataset, dataset.id)
        dataset_grant = session.scalar(
            select(Grant).where(
                Grant.resource_type == "dataset",
                Grant.resource_id == dataset.id,
                Grant.subject_type == "user",
                Grant.subject_id == admin.id,
            )
        )
    assert field.id != "attacker-id"
    assert field.dataset_id == dataset.id
    assert field.created_at.year != 1900 and field.updated_at.year != 1900
    assert field.is_required is True and field.is_unique is True
    assert persisted_dataset.owner_user_id == admin.id
    assert dataset_grant.permission == "admin"


@pytest.mark.parametrize(
    "overrides",
    [
        {"key": "INVALID"},
        {"name": "   "},
        {"data_type": "datetime"},
    ],
)
def test_create_field_validation_is_safe_and_preserves_values(dataset_browser, overrides):
    factory, services, admin, client = dataset_browser
    create_dataset(services, admin, "invalid-field")
    login(client)
    form = request(client, "get", "/datasets/invalid-field/fields/new")
    markup = "<script>alert(1)</script>"
    data = {
        "key": "valid_key",
        "name": markup,
        "data_type": "string",
        "csrf_token": form_csrf(form),
        **overrides,
    }

    response = request(client, "post", "/datasets/invalid-field/fields", data=data)

    assert response.status_code == 400
    assert markup not in body(response)
    if overrides.get("name") != "   ":
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body(response)
    with factory() as session:
        assert session.scalar(select(func.count(DatasetField.id))) == 0


def test_duplicate_field_key_is_safe_conflict(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "duplicate-field")
    create_field(services, admin, dataset, "duplicate")
    login(client)
    form = request(client, "get", "/datasets/duplicate-field/fields/new")

    response = request(
        client,
        "post",
        "/datasets/duplicate-field/fields",
        data={
            "key": "duplicate",
            "name": "Duplicate",
            "data_type": "string",
            "csrf_token": form_csrf(form),
        },
    )

    assert response.status_code == 409
    assert "Field key already exists in this Dataset" in body(response)
    assert "constraint" not in body(response).casefold()
    assert "sql" not in body(response).casefold()


@pytest.mark.parametrize("token", [None, "invalid", "é"])
def test_create_field_requires_valid_csrf(dataset_browser, token):
    factory, services, admin, client = dataset_browser
    create_dataset(services, admin, "csrf-field")
    login(client)
    data = {"key": "blocked", "name": "Blocked", "data_type": "string"}
    if token is not None:
        data["csrf_token"] = token

    response = request(client, "post", "/datasets/csrf-field/fields", data=data)

    assert response.status_code == 403
    with factory() as session:
        assert session.scalar(select(func.count(DatasetField.id))) == 0


@pytest.mark.parametrize("permission", ["viewer", "editor"])
def test_viewer_and_editor_field_mutation_routes_are_denied(dataset_browser, permission):
    factory, services, admin, client = dataset_browser
    user = services.users.create_user(
        admin.id, f"blocked-{permission}@example.com", permission.title(), "user password"
    )
    dataset = create_dataset(services, admin, "blocked-schema")
    field = create_field(services, admin, dataset)
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, permission)
    login(client, f"blocked-{permission}@example.com", "user password")
    csrf = session_csrf(client, services)

    responses = [
        request(client, "get", "/datasets/blocked-schema/fields/new"),
        request(
            client,
            "post",
            "/datasets/blocked-schema/fields",
            data={
                "key": "blocked",
                "name": "Blocked",
                "data_type": "string",
                "csrf_token": csrf,
            },
        ),
        request(client, "get", f"/datasets/blocked-schema/fields/{field.key}/edit"),
        request(
            client,
            "post",
            f"/datasets/blocked-schema/fields/{field.key}",
            data={
                "name": "Blocked",
                "data_type": "string",
                "csrf_token": csrf,
            },
        ),
    ]

    assert [response.status_code for response in responses] == [404] * 4
    assert len({body(response) for response in responses}) == 1
    with factory() as session:
        persisted = session.get(DatasetField, field.id)
        assert persisted.name == "Field"
        assert session.scalar(select(func.count(DatasetField.id))) == 1


def test_admin_edit_field_flow_is_allowlisted_and_key_is_immutable(dataset_browser):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "edit-field")
    field = create_field(services, admin, dataset, "stable_key", "Original")
    original_id = field.id
    login(client)
    form = request(client, "get", "/datasets/edit-field/fields/stable_key/edit")

    assert form.status_code == 200
    assert "stable_key" in body(form)
    assert "name='key'" not in body(form)
    response = request(
        client,
        "post",
        "/datasets/edit-field/fields/stable_key",
        data={
            "name": "  Changed  ",
            "data_type": "date",
            "is_required": "on",
            "is_unique": "on",
            "csrf_token": form_csrf(form),
            "key": "attacker_key",
            "id": "attacker-id",
            "dataset_id": "attacker-dataset",
            "created_at": "1900-01-01",
            "updated_at": "1900-01-01",
            "permission": "viewer",
            "owner_user_id": "attacker-owner",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/datasets/edit-field")
    with factory() as session:
        persisted = session.get(DatasetField, original_id)
        assert persisted.key == "stable_key"
        assert persisted.name == "Changed"
        assert persisted.data_type == "date"
        assert persisted.is_required is True and persisted.is_unique is True
        assert persisted.dataset_id == dataset.id
        assert persisted.created_at.year != 1900 and persisted.updated_at.year != 1900
        assert (
            session.scalar(
                select(func.count(DatasetField.id)).where(DatasetField._key == "attacker_key")
            )
            == 0
        )


def test_edit_field_absent_checkboxes_clear_flags(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "checkbox-edit")
    field = create_field(services, admin, dataset, is_required=True, is_unique=True)
    login(client)
    form = request(client, "get", "/datasets/checkbox-edit/fields/field/edit")

    response = request(
        client,
        "post",
        "/datasets/checkbox-edit/fields/field",
        data={
            "name": "Field",
            "data_type": "string",
            "csrf_token": form_csrf(form),
        },
    )

    assert response.status_code == 302
    persisted = services.dataset_fields.get_field(admin.id, dataset.key, field.key)
    assert persisted.is_required is False and persisted.is_unique is False


def test_browser_noop_field_update_preserves_timestamp_and_audit(dataset_browser):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "noop-field")
    field = create_field(services, admin, dataset, name="Ñame")
    with factory() as session:
        original_updated_at = session.get(DatasetField, field.id).updated_at
    login(client)
    form = request(client, "get", "/datasets/noop-field/fields/field/edit")

    response = request(
        client,
        "post",
        "/datasets/noop-field/fields/field",
        data={
            "name": " N\N{COMBINING TILDE}ame ",
            "data_type": "string",
            "csrf_token": form_csrf(form),
        },
    )

    assert response.status_code == 302
    with factory() as session:
        persisted = session.get(DatasetField, field.id)
        assert persisted.updated_at == original_updated_at
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_id == field.id,
                    AuditEvent.action == "definition_updated",
                )
            )
            == 0
        )


@pytest.mark.parametrize("token", [None, "invalid", "é"])
def test_edit_field_requires_valid_csrf(dataset_browser, token):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "csrf-field-edit")
    field = create_field(services, admin, dataset, name="Original")
    login(client)
    data = {"name": "Changed", "data_type": "string"}
    if token is not None:
        data["csrf_token"] = token

    response = request(client, "post", "/datasets/csrf-field-edit/fields/field", data=data)

    assert response.status_code == 403
    with factory() as session:
        assert session.get(DatasetField, field.id).name == "Original"


def test_invalid_field_edit_redisplays_escaped_value_without_mutation(dataset_browser):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "invalid-field-edit")
    field = create_field(services, admin, dataset, name="Original")
    login(client)
    form = request(client, "get", "/datasets/invalid-field-edit/fields/field/edit")
    markup = "<script>alert(1)</script>"

    response = request(
        client,
        "post",
        "/datasets/invalid-field-edit/fields/field",
        data={
            "name": markup,
            "data_type": "datetime",
            "csrf_token": form_csrf(form),
        },
    )

    assert response.status_code == 400
    assert markup not in body(response)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body(response)
    with factory() as session:
        persisted = session.get(DatasetField, field.id)
        assert persisted.name == "Original"
        assert persisted.data_type == "string"


def test_field_routes_are_non_disclosing_for_missing_malformed_cross_dataset_and_inaccessible(
    dataset_browser,
):
    _, services, admin, client = dataset_browser
    private = create_dataset(services, admin, "private-schema")
    other = create_dataset(services, admin, "other-schema")
    create_field(services, admin, private, "private_field")
    user = services.users.create_user(admin.id, "outsider@example.com", "Outsider", "user password")
    services.grants.create_grant(admin.id, "user", user.id, "dataset", other.id, "viewer")
    login(client, "outsider@example.com", "user password")

    responses = [
        request(client, "get", "/datasets/private-schema/fields/private_field/edit"),
        request(client, "get", "/datasets/missing/fields/private_field/edit"),
        request(client, "get", "/datasets/INVALID/fields/private_field/edit"),
        request(client, "get", "/datasets/other-schema/fields/missing/edit"),
        request(client, "get", "/datasets/other-schema/fields/_malformed/edit"),
        request(client, "get", "/datasets/other-schema/fields/private_field/edit"),
    ]

    assert [response.status_code for response in responses] == [404] * 6
    assert len({body(response) for response in responses}) == 1

    login(client)
    authorized_parent_responses = [
        request(client, "get", "/datasets/other-schema/fields/missing/edit"),
        request(client, "get", "/datasets/other-schema/fields/_malformed/edit"),
        request(client, "get", "/datasets/other-schema/fields/private_field/edit"),
    ]
    assert [response.status_code for response in authorized_parent_responses] == [404] * 3
    assert len({body(response) for response in authorized_parent_responses}) == 1


def test_create_field_post_is_non_disclosing_and_does_not_mutate(dataset_browser):
    factory, services, admin, client = dataset_browser
    private = create_dataset(services, admin, "private-create")
    services.users.create_user(admin.id, "create-outsider@example.com", "Outsider", "user password")
    login(client, "create-outsider@example.com", "user password")
    csrf = session_csrf(client, services)
    data = {
        "key": "attempted",
        "name": "Attempted",
        "data_type": "string",
        "csrf_token": csrf,
    }

    responses = [
        request(client, "post", "/datasets/missing/fields", data=data),
        request(client, "post", "/datasets/INVALID/fields", data=data),
        request(client, "post", f"/datasets/{private.key}/fields", data=data),
    ]

    assert [response.status_code for response in responses] == [404] * 3
    assert len({body(response) for response in responses}) == 1
    assert all("Traceback" not in body(response) for response in responses)
    with factory() as session:
        assert session.scalar(select(func.count(DatasetField.id))) == 0
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "dataset_field")
            )
            == 0
        )


def test_update_field_post_is_non_disclosing_and_does_not_mutate(dataset_browser):
    factory, services, admin, client = dataset_browser
    private = create_dataset(services, admin, "private-update")
    visible = create_dataset(services, admin, "visible-update")
    private_field = create_field(services, admin, private, "private_field", "Original")
    outsider = services.users.create_user(
        admin.id, "update-outsider@example.com", "Outsider", "user password"
    )
    services.grants.create_grant(admin.id, "user", outsider.id, "dataset", visible.id, "admin")
    login(client, "update-outsider@example.com", "user password")
    csrf = session_csrf(client, services)
    data = {"name": "Changed", "data_type": "date", "csrf_token": csrf}

    responses = [
        request(client, "post", "/datasets/missing/fields/private_field", data=data),
        request(client, "post", "/datasets/INVALID/fields/private_field", data=data),
        request(client, "post", "/datasets/private-update/fields/private_field", data=data),
        request(client, "post", "/datasets/visible-update/fields/missing", data=data),
        request(client, "post", "/datasets/visible-update/fields/_malformed", data=data),
        request(client, "post", "/datasets/visible-update/fields/private_field", data=data),
    ]

    assert [response.status_code for response in responses] == [404] * 6
    assert len({body(response) for response in responses}) == 1
    assert all("Traceback" not in body(response) for response in responses)
    with factory() as session:
        persisted = session.get(DatasetField, private_field.id)
        assert persisted.name == "Original"
        assert persisted.data_type == "string"
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_type == "dataset_field",
                    AuditEvent.action == "definition_updated",
                )
            )
            == 0
        )


def test_record_identifier_detail_is_compact_canonical_and_marks_each_component(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "business-key")
    create_field(services, admin, dataset, "zeta", "Zeta", is_required=True)
    create_field(services, admin, dataset, "alpha", "Alpha", is_required=True)
    login(client)

    unconfigured = body(request(client, "get", "/datasets/business-key"))
    assert "No record identifier configured" in unconfigured
    assert "Business key" not in unconfigured

    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("zeta", "alpha"))
    configured = body(request(client, "get", "/datasets/business-key"))
    assert "No record identifier configured" not in configured
    assert configured.index("Zeta") < configured.index("Alpha")
    assert configured.count("images/lucide-key-round.svg") == 2
    assert "Alpha" in configured
    assert "Zeta" in configured


@pytest.mark.parametrize("permission", ["viewer", "editor"])
def test_viewer_and_editor_see_record_identifier_without_management_controls(
    dataset_browser, permission
):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, f"{permission}-visible")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    user = services.users.create_user(
        admin.id, f"{permission}@example.com", permission.title(), "user password"
    )
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, permission)
    login(client, user.email, "user password")

    detail = body(request(client, "get", f"/datasets/{dataset.key}"))
    assert "<code class='field-key'>code</code>" in detail
    assert "Code is part of the record identifier" in detail
    assert "Code" in detail
    assert "Edit field" not in detail
    field = services.dataset_fields.get_field(admin.id, dataset.key, "code")
    with pytest.raises(NotFoundError):
        services.dataset_fields.delete_field(user.id, dataset.key, field.key)


def test_dataset_admin_adds_and_removes_identifier_membership_from_edit_field_with_prg(
    dataset_browser,
):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "managed-key")
    required = create_field(services, admin, dataset, "required", "Required", is_required=True)
    optional = create_field(services, admin, dataset, "optional", "Optional")
    manager = services.users.create_user(
        admin.id, "manager@example.com", "Manager", "user password"
    )
    services.grants.create_grant(admin.id, "user", manager.id, "dataset", dataset.id, "admin")
    login(client, manager.email, "user password")

    form = request(client, "get", f"/datasets/{dataset.key}/fields/{required.key}/edit")
    page = body(form)
    assert "Part of record identifier" in page
    assert "Delete field" in page
    response = request(
        client,
        "post",
        f"/datasets/{dataset.key}/fields/{required.key}",
        data={
            "name": required.name,
            "data_type": required.data_type,
            "is_required": "on",
            "is_business_key_component": "on",
            "csrf_token": form_csrf(form),
            "dataset_id": "attacker-controlled",
            "owner_user_id": manager.id,
            "permission": "viewer",
            "business_key": optional.key,
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/datasets/{dataset.key}")
    assert services.dataset_business_keys.get_business_key(manager.id, dataset.key).field_keys == (
        required.key,
    )

    change_form = request(client, "get", f"/datasets/{dataset.key}/fields/{required.key}/edit")
    assert "name='is_business_key_component' checked" in body(change_form)
    changed = request(
        client,
        "post",
        f"/datasets/{dataset.key}/fields/{required.key}",
        data={
            "name": required.name,
            "data_type": required.data_type,
            "is_required": "on",
            "csrf_token": form_csrf(change_form),
        },
    )
    assert changed.status_code == 302
    assert services.dataset_business_keys.get_business_key(manager.id, dataset.key).field_keys == ()
    with factory() as session:
        persisted_dataset = session.get(Dataset, dataset.id)
        persisted_optional = session.get(DatasetField, optional.id)
        assert persisted_dataset.owner_user_id == admin.id
        assert persisted_optional.is_required is False


def test_admin_can_add_field_directly_to_record_identifier(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "create-identifier-field")
    login(client)
    form = request(client, "get", f"/datasets/{dataset.key}/fields/new")
    assert "Part of record identifier" in body(form)

    response = request(
        client,
        "post",
        f"/datasets/{dataset.key}/fields",
        data={
            "key": "code",
            "name": "Code",
            "data_type": "string",
            "is_required": "on",
            "is_business_key_component": "on",
            "csrf_token": form_csrf(form),
        },
    )

    assert response.status_code == 302
    field = services.dataset_fields.get_field(admin.id, dataset.key, "code")
    assert field.is_required is True
    assert services.dataset_business_keys.get_business_key(admin.id, dataset.key).field_keys == (
        "code",
    )


def test_add_field_rejects_identifier_membership_without_required(dataset_browser):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "invalid-create-identifier")
    login(client)
    form = request(client, "get", f"/datasets/{dataset.key}/fields/new")

    response = request(
        client,
        "post",
        f"/datasets/{dataset.key}/fields",
        data={
            "key": "code",
            "name": "Code",
            "data_type": "string",
            "is_business_key_component": "on",
            "csrf_token": form_csrf(form),
        },
    )

    assert response.status_code == 400
    assert "Record identifier field must be required" in body(response)
    assert "name='is_business_key_component' checked" in body(response)
    with factory() as session:
        assert session.scalar(select(func.count(DatasetField.id))) == 0
    assert services.dataset_business_keys.get_business_key(admin.id, dataset.key).field_keys == ()


def test_community_admin_builds_composite_identifier_without_dataset_grant(dataset_browser):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "community-key")
    create_field(services, admin, dataset, "first", "First", is_required=True)
    create_field(services, admin, dataset, "second", "Second", is_required=True)
    with factory.begin() as session:
        session.query(Grant).filter_by(resource_type="dataset", resource_id=dataset.id).delete()
    login(client)

    for field_key in ("second", "first"):
        form = request(client, "get", f"/datasets/{dataset.key}/fields/{field_key}/edit")
        assert form.status_code == 200
        changed = request(
            client,
            "post",
            f"/datasets/{dataset.key}/fields/{field_key}",
            data={
                "name": field_key.title(),
                "data_type": "string",
                "is_required": "on",
                "is_business_key_component": "on",
                "csrf_token": form_csrf(form),
            },
        )
        assert changed.status_code == 302
    assert services.dataset_business_keys.get_business_key(admin.id, dataset.key).field_keys == (
        "first",
        "second",
    )


@pytest.mark.parametrize("token", [None, "invalid", "é"])
def test_delete_field_requires_valid_csrf(dataset_browser, token):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "csrf-key")
    field = create_field(services, admin, dataset, "field", "Field", is_required=True)
    login(client)
    data = {}
    if token is not None:
        data["csrf_token"] = token

    response = request(
        client, "post", f"/datasets/{dataset.key}/fields/{field.key}/delete", data=data
    )

    assert response.status_code == 403
    with factory() as session:
        assert session.get(DatasetField, field.id) is not None
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_id == field.id,
                    AuditEvent.action == "deleted",
                )
            )
            == 0
        )


def test_identifier_membership_requires_field_to_remain_required(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "eligibility")
    optional = create_field(services, admin, dataset, "optional", "Optional")
    login(client)
    form = request(client, "get", f"/datasets/{dataset.key}/fields/{optional.key}/edit")
    response = request(
        client,
        "post",
        f"/datasets/{dataset.key}/fields/{optional.key}",
        data={
            "name": optional.name,
            "data_type": optional.data_type,
            "is_business_key_component": "on",
            "csrf_token": form_csrf(form),
        },
    )
    assert response.status_code == 400
    assert "Record identifier field must be required" in body(response)
    assert services.dataset_business_keys.get_business_key(admin.id, dataset.key).field_keys == ()


def test_delete_field_is_non_disclosing_for_missing_inaccessible_and_owner_only_datasets(
    dataset_browser,
):
    factory, services, admin, client = dataset_browser
    user = services.users.create_user(admin.id, "outsider@example.com", "Outsider", "user password")
    private = create_dataset(services, admin, "private-key")
    private_field = create_field(services, admin, private, "private_field", "Private")
    with factory.begin() as session:
        session.execute(
            update(Dataset).where(Dataset.id == private.id).values(owner_user_id=user.id)
        )
    login(client, user.email, "user password")
    csrf = session_csrf(client, services)

    responses = [
        request(
            client,
            "post",
            "/datasets/missing/fields/private_field/delete",
            data={"csrf_token": csrf},
        ),
        request(
            client,
            "post",
            "/datasets/INVALID/fields/private_field/delete",
            data={"csrf_token": csrf},
        ),
        request(
            client,
            "post",
            f"/datasets/{private.key}/fields/{private_field.key}/delete",
            data={"csrf_token": csrf},
        ),
    ]
    assert [response.status_code for response in responses] == [404] * 3
    assert len({body(response) for response in responses}) == 1
    with factory() as session:
        assert session.get(DatasetField, private_field.id) is not None


def test_identifier_field_content_is_escaped_on_detail_and_edit_form(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "escaped-key")
    unsafe = "<img src=x onerror=alert(1)>"
    create_field(services, admin, dataset, "unsafe", unsafe, is_required=True)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("unsafe",))
    login(client)

    for response in (
        request(client, "get", f"/datasets/{dataset.key}"),
        request(client, "get", f"/datasets/{dataset.key}/fields/unsafe/edit"),
    ):
        assert response.status_code == 200
        assert unsafe not in body(response)
        assert "&lt;img src=x onerror=alert(1)&gt;" in body(response)


def test_dataset_admin_deletes_normal_field_with_prg_and_audit(dataset_browser):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "delete-field")
    field = create_field(services, admin, dataset, "obsolete", "Obsolete")
    login(client)
    edit_form = request(client, "get", f"/datasets/{dataset.key}/fields/{field.key}/edit")

    response = request(
        client,
        "post",
        f"/datasets/{dataset.key}/fields/{field.key}/delete",
        data={"csrf_token": form_csrf(edit_form), "dataset_id": "attacker-controlled"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/datasets/{dataset.key}")
    with factory() as session:
        assert session.get(DatasetField, field.id) is None
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_type == "dataset_field",
                    AuditEvent.object_id == field.id,
                    AuditEvent.action == "deleted",
                )
            )
            == 1
        )


def test_community_admin_deletes_normal_field_without_dataset_grant(dataset_browser):
    factory, services, admin, client = dataset_browser
    community_admin = services.users.create_user(
        admin.id, "second-admin@example.com", "Second Admin", "user password", role="admin"
    )
    dataset = create_dataset(services, admin, "community-delete")
    field = create_field(services, admin, dataset, "obsolete", "Obsolete")
    with factory.begin() as session:
        session.query(Grant).filter_by(resource_type="dataset", resource_id=dataset.id).delete()
    login(client, community_admin.email, "user password")

    edit_form = request(client, "get", f"/datasets/{dataset.key}/fields/{field.key}/edit")
    response = request(
        client,
        "post",
        f"/datasets/{dataset.key}/fields/{field.key}/delete",
        data={"csrf_token": form_csrf(edit_form)},
    )

    assert response.status_code == 302
    with factory() as session:
        assert session.get(DatasetField, field.id) is None


@pytest.mark.parametrize("permission", ["viewer", "editor"])
def test_viewer_and_editor_cannot_delete_field(dataset_browser, permission):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, f"{permission}-delete")
    field = create_field(services, admin, dataset, "protected", "Protected")
    user = services.users.create_user(
        admin.id, f"{permission}-delete@example.com", permission.title(), "user password"
    )
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, permission)
    login(client, user.email, "user password")

    response = request(
        client,
        "post",
        f"/datasets/{dataset.key}/fields/{field.key}/delete",
        data={"csrf_token": session_csrf(client, services)},
    )

    assert response.status_code == 404
    with factory() as session:
        assert session.get(DatasetField, field.id) is not None


def test_identifier_field_cannot_be_deleted_and_state_remains_intact(dataset_browser):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "identifier-delete")
    field = create_field(services, admin, dataset, "code", "Code", is_required=True)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, (field.key,))
    login(client)
    edit_form = request(client, "get", f"/datasets/{dataset.key}/fields/{field.key}/edit")

    response = request(
        client,
        "post",
        f"/datasets/{dataset.key}/fields/{field.key}/delete",
        data={"csrf_token": form_csrf(edit_form)},
    )

    assert response.status_code == 400
    assert "Remove this field from the Record identifier before deleting it" in body(response)
    with factory() as session:
        assert session.get(DatasetField, field.id) is not None
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_id == field.id,
                    AuditEvent.action == "deleted",
                )
            )
            == 0
        )
    assert services.dataset_business_keys.get_business_key(admin.id, dataset.key).field_keys == (
        field.key,
    )


def test_delete_audit_failure_rolls_back_field(dataset_browser, monkeypatch):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "delete-rollback")
    field = create_field(services, admin, dataset, "field", "Field")
    login(client)
    edit_form = request(client, "get", f"/datasets/{dataset.key}/fields/{field.key}/edit")

    def fail_audit(_self, **_values):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", fail_audit)
    response = request(
        client,
        "post",
        f"/datasets/{dataset.key}/fields/{field.key}/delete",
        data={"csrf_token": form_csrf(edit_form)},
    )
    assert response.status_code == 500

    with factory() as session:
        assert session.get(DatasetField, field.id) is not None
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_id == field.id,
                    AuditEvent.action == "deleted",
                )
            )
            == 0
        )


@pytest.mark.parametrize("permission", ["viewer", "editor", "admin"])
def test_configuration_panel_triggers_follow_existing_permissions(dataset_browser, permission):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "panel-permissions")
    create_field(services, admin, dataset)
    user = services.users.create_user(admin.id, "panels@example.com", "Panels", PASSWORD)
    services.grants.create_grant(admin.id, "user", user.id, "dataset", dataset.id, permission)
    login(client, user.email)

    page = body(request(client, "get", f"/datasets/{dataset.key}"))
    expected = {"viewer": 0, "editor": 1, "admin": 3}[permission]
    assert page.count("data-panel-url") == expected
    assert page.count("id='configuration-panel'") == (1 if expected else 0)
    assert "data-panel-url" not in body(request(client, "get", "/datasets"))


def test_configuration_flows_load_one_shared_panel_and_keep_full_page_fallback(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "panel-forms", "Name <unsafe>")
    create_field(services, admin, dataset)
    login(client)
    loaded = []
    original_loader = client.application.jinja_env.loader

    class TrackingLoader(BaseLoader):
        def get_source(self, environment, template):
            loaded.append(template)
            return original_loader.get_source(environment, template)

    client.application.jinja_env.loader = TrackingLoader()
    for page_path in ["/datasets", "/datasets/panel-forms"]:
        page = body(request(client, "get", page_path))
        assert page.count("id='configuration-panel'") == 1
        assert "aria-labelledby='configuration-panel-title'" in page
        assert "id='configuration-panel-title' tabindex='-1'" in page
        assert "data-panel-host" in page
    assert "components/side_panel.html" in loaded
    for path, title in [
        ("/datasets/new", "New dataset"),
        ("/datasets/panel-forms/edit", "Edit dataset"),
        ("/datasets/panel-forms/fields/new", "Add field"),
        ("/datasets/panel-forms/fields/field/edit", "Edit field"),
    ]:
        response = request(client, "get", path)
        page = body(response)
        assert response.status_code == 200
        assert "<!doctype html>" in page
        assert f"data-side-panel-content data-panel-title='{title}'" in page
        if title in {"New dataset", "Add field"}:
            assert "data-panel-cancel href=" in page
        else:
            assert "data-panel-cancel" not in page
            assert "side-panel-footer" in page
        assert "method='post'" in page
        assert form_csrf(response)
        assert "Name <unsafe>" not in page


def test_side_panel_description_gets_an_accessible_id_without_explicit_id(dataset_browser):
    _, _, _, client = dataset_browser
    with client.application.test_request_context():
        template = client.application.jinja_env.from_string(
            '{% from "components/side_panel.html" import side_panel %}'
            '{% call side_panel("first", "First", "first-title", description="Help <text>") %}'
            "<p>Content</p>{% endcall %}"
            '{% call side_panel("second", "Second", "second-title") %}Body{% endcall %}'
        )
        page = template.render()
    assert "aria-describedby='first-description'" in page
    assert "id='first-description'" in page
    assert "Help &lt;text&gt;" in page
    assert page.count("aria-describedby=") == 1


@pytest.mark.parametrize(
    "path, data",
    [
        ("/datasets", {"key": "invalid key", "name": "<script>alert(1)</script>"}),
        ("/datasets/panel-errors", {"name": "", "description": "<script>alert(1)</script>"}),
        ("/datasets/panel-errors/fields", {"key": "bad key", "name": "<script>alert(1)</script>"}),
        (
            "/datasets/panel-errors/fields/field",
            {"name": "<script>alert(1)</script>", "data_type": "invalid"},
        ),
    ],
)
def test_panel_validation_keeps_server_errors_values_and_csrf(dataset_browser, path, data):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "panel-errors")
    create_field(services, admin, dataset)
    login(client)
    csrf = session_csrf(client, services)
    rejected = request(client, "post", path, data=data)
    assert rejected.status_code == 403
    assert "data-side-panel-content" not in body(rejected)
    response = request(client, "post", path, data={**data, "csrf_token": csrf})
    page = body(response)
    assert response.status_code == 400
    assert "data-side-panel-content" in page
    assert "aria-invalid='true'" in page or "data-error-focus" in page
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert form_csrf(response) == csrf


@pytest.mark.parametrize("direction", ["up", "down"])
@pytest.mark.parametrize("permission", ["viewer", "editor", None])
def test_reorder_rejects_unauthorized_posts_without_order_or_audit_change(
    dataset_browser, direction, permission
):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "protected-order")
    fields = [create_field(services, admin, dataset, key, key) for key in ("a", "b", "c")]
    actor = services.users.create_user(admin.id, "reader@example.com", "Reader", "user password")
    if permission:
        services.grants.create_grant(admin.id, "user", actor.id, "dataset", dataset.id, permission)
    login(client, actor.email, "user password")
    response = request(
        client,
        "post",
        "/datasets/protected-order/fields/order",
        data={
            "csrf_token": session_csrf(client, services),
            "expected_field_id": [field.id for field in fields],
            "field_id": fields[1].id,
            "direction": direction,
        },
    )
    assert response.status_code == 404
    assert body(response) == body(request(client, "get", "/does-not-exist"))
    assert [field.id for field in services.dataset_fields.list_fields(admin.id, dataset.key)] == [
        field.id for field in fields
    ]
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.action == "field_order_updated")
            )
            == 0
        )


@pytest.mark.parametrize("direction", ["up", "down"])
@pytest.mark.parametrize("token", [None, "invalid", "other-session"])
def test_reorder_requires_session_bound_csrf_without_order_or_audit_change(
    dataset_browser, direction, token
):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "csrf-order")
    fields = [create_field(services, admin, dataset, key, key) for key in ("a", "b", "c")]
    login(client)
    data = {
        "expected_field_id": [field.id for field in fields],
        "field_id": fields[1].id,
        "direction": direction,
    }
    if token == "other-session":
        other_client = client.application.test_client()
        login(other_client)
        token = session_csrf(other_client, services)
    if token is not None:
        data["csrf_token"] = token
    response = request(client, "post", "/datasets/csrf-order/fields/order", data=data)
    assert response.status_code == 403
    assert [field.id for field in services.dataset_fields.list_fields(admin.id, dataset.key)] == [
        field.id for field in fields
    ]
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.action == "field_order_updated")
            )
            == 0
        )

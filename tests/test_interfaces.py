import json
import os
import re
import uuid
from itertools import chain, combinations
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.pool import StaticPool
from support import create_dataset, html_elements, html_tag_texts

from refrncr.app import create_application, create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, LoginSession, Membership, Team, User
from refrncr.services import AuthorizationError, ResourceTypeRegistry
from refrncr.web import ANONYMOUS_CSRF_COOKIE, SESSION_COOKIE, create_flask_app

HTTPS = "https://localhost"
ADMIN_PASSWORD = "correct horse battery staple"


def configured_services(*, include_dataset_dependencies=False):
    engine = create_database_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    configured = create_community_services(factory, ResourceTypeRegistry())
    users = configured.users
    authentication = configured.authentication
    sessions = configured.sessions
    teams = configured.teams
    memberships = configured.memberships
    users.bootstrap_organisation()
    configured_services = factory, users, authentication, sessions, teams, memberships
    if include_dataset_dependencies:
        return (
            *configured_services,
            configured.authorization,
            configured.datasets,
            configured.dataset_fields,
            configured.dataset_business_keys,
        )
    return configured_services


@pytest.fixture
def interface():
    factory, users, authentication, sessions, teams, memberships = configured_services()
    client = create_flask_app(users, authentication, sessions, teams, memberships).test_client()
    return factory, users, authentication, sessions, teams, memberships, client


def request(client, method, path, **kwargs):
    return getattr(client, method)(path, base_url=HTTPS, **kwargs)


def response_text(response):
    if hasattr(response, "get_data"):
        return response.get_data(as_text=True)
    return response.text


def csrf_token(response):
    match = re.search(r"name='csrf_token' value='([^']+)'", response_text(response))
    assert match is not None
    return match.group(1)


def setup_admin(client):
    token = csrf_token(request(client, "get", "/setup"))
    response = request(
        client,
        "post",
        "/setup",
        data={
            "email": "Admin@Example.com",
            "display_name": "Administrator",
            "password": ADMIN_PASSWORD,
            "csrf_token": token,
        },
    )
    assert response.status_code == 302


def login(client, email="admin@example.com", password=ADMIN_PASSWORD):
    token = csrf_token(request(client, "get", "/login"))
    return request(
        client,
        "post",
        "/login",
        data={"email": email, "password": password, "csrf_token": token},
    )


def test_theme_preference_is_csrf_protected_and_server_rendered(interface):
    _, _, _, _, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302

    page = request(client, "get", "/")
    assert 'data-theme="' not in response_text(page)
    token = authenticated_csrf(client)

    response = request(
        client,
        "post",
        "/settings/theme",
        data={"theme_preference": "dark", "csrf_token": token},
    )
    assert response.status_code == 303
    assert response.headers["Location"] == "/settings/appearance"
    appearance = request(client, "get", "/settings/appearance")
    assert appearance.status_code == 200
    assert 'value="dark"' in response_text(appearance)
    assert re.search(r'value="dark"[^>]*checked', response_text(appearance))
    assert "Theme saved." not in response_text(appearance)

    dark_page = request(client, "get", "/")
    assert '<html lang="en" data-theme="dark">' in response_text(dark_page)
    assert "images/refrncr-wordmark-reversed.svg" in response_text(dark_page)
    assert 'class="account-theme"' not in response_text(dark_page)

    token = authenticated_csrf(client)
    response = request(
        client,
        "post",
        "/settings/theme",
        data={"theme_preference": "system", "csrf_token": token},
    )
    assert response.status_code == 303
    assert 'data-theme="' not in response_text(request(client, "get", "/"))

    token = authenticated_csrf(client)
    response = request(
        client,
        "post",
        "/settings/theme",
        data={"theme_preference": "unknown", "csrf_token": token},
    )
    assert response.status_code == 400

    response = request(client, "post", "/settings/theme", data={"theme_preference": "dark"})
    assert response.status_code == 403


def test_every_browser_route_declares_a_known_capability_or_public_access(interface):
    client = interface[-1]
    known = {
        "can_access_community",
        "can_administer_installation",
        "can_manage_access",
        "can_view_dataset",
        "can_maintain_dataset",
        "can_export_dataset",
        "can_create_dataset",
        "can_manage_export_access",
    }
    for rule in client.application.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        view = client.application.view_functions[rule.endpoint]
        if getattr(view, "public_route", False):
            continue
        assert getattr(view, "required_capability", None) in known, rule.endpoint


def _browser_route_contract(app):
    actual = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        view = app.view_functions[rule.endpoint]
        actual.append(
            {
                "path": rule.rule,
                "methods": sorted(rule.methods - {"HEAD", "OPTIONS"}),
                "capability": getattr(view, "required_capability", None),
                "public": getattr(view, "public_route", False),
                # Blueprint prefixes are the one intentional endpoint change in REF-224.
                "endpoint": rule.endpoint.rsplit(".", 1)[-1],
            }
        )
    return sorted(actual, key=lambda row: (row["path"], row["methods"], row["endpoint"]))


def test_browser_route_contract_matches_the_pre_blueprint_snapshot():
    (
        _,
        users,
        authentication,
        sessions,
        teams,
        memberships,
        authorization,
        datasets,
        dataset_fields,
        dataset_business_keys,
    ) = configured_services(include_dataset_dependencies=True)
    app = create_flask_app(
        users,
        authentication,
        sessions,
        teams,
        memberships,
        authorization=authorization,
        datasets=datasets,
        dataset_fields=dataset_fields,
        dataset_business_keys=dataset_business_keys,
    )
    expected = json.loads((Path(__file__).parent / "fixtures" / "web_routes.json").read_text())

    assert _browser_route_contract(app) == expected


def test_browser_route_contract_without_dataset_ui_matches_snapshot():
    factory, users, authentication, sessions, teams, memberships = configured_services()
    app = create_flask_app(users, authentication, sessions, teams, memberships)
    expected = json.loads(
        (Path(__file__).parent / "fixtures" / "web_routes_without_datasets.json").read_text()
    )

    assert _browser_route_contract(app) == expected
    assert app.test_client().get("/attention", follow_redirects=False).status_code == 404


def test_renamed_views_keep_csrf_and_capability_checks():
    (
        _,
        users,
        authentication,
        sessions,
        teams,
        memberships,
        authorization,
        datasets,
        dataset_fields,
        dataset_business_keys,
    ) = configured_services(include_dataset_dependencies=True)
    app = create_flask_app(
        users,
        authentication,
        sessions,
        teams,
        memberships,
        authorization=authorization,
        datasets=datasets,
        dataset_fields=dataset_fields,
        dataset_business_keys=dataset_business_keys,
    )
    app.add_url_rule(
        "/renamed-teams/<team_id>",
        endpoint="renamed_team_view",
        view_func=app.view_functions["teams.rename_team"],
        methods=["POST"],
    )
    app.add_url_rule(
        "/renamed-datasets/<key>/fields",
        endpoint="renamed_dataset_field_view",
        view_func=app.view_functions["datasets.create_dataset_field"],
        methods=["POST"],
    )
    client = app.test_client()
    setup_admin(client)
    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    team = teams.create_team(admin.id, "Before rename")
    dataset = datasets.create_dataset(admin.id, "renamed-dataset", "Renamed dataset", "")

    for path in (
        f"/renamed-teams/{team.id}",
        f"/renamed-datasets/{dataset.key}/fields",
    ):
        response = request(client, "post", path, data={"name": "After rename"})
        assert response.status_code == 403

    regular = users.create_user(
        admin.id, "renamed-regular@example.com", "Regular", "valid password"
    )
    assert login(client, regular.email, "valid password").status_code == 302
    token = sessions.resolve_with_session(client.get_cookie(SESSION_COOKIE).value)[1].csrf_token
    response = request(
        client,
        "post",
        f"/renamed-teams/{team.id}",
        data={"name": "After rename", "csrf_token": token},
    )
    assert response.status_code == 403
    response = request(
        client,
        "post",
        f"/renamed-datasets/{dataset.key}/fields",
        data={"name": "Field", "csrf_token": token},
    )
    assert response.status_code == 404


def _route_matrix_expectations():
    community = {
        "overview.attention_overview",
        "settings.api_key_list",
        "settings.api_key_create",
        "settings.api_key_revoke",
        "settings.appearance_settings",
        "datasets.dataset_list",
        "overview.index",
        "teams.team_list",
        "settings.theme_preference",
    }
    installation_admin = {
        "audit.audit_list",
        "audit.audit_detail",
        "settings.dataset_access_settings",
    }
    access_admin = {
        "users.user_list",
        "users.create_user",
        "users.change_user_state",
        "users.change_user_email",
        "teams.create_team",
        "teams.rename_team",
        "teams.delete_team",
        "teams.team_members",
        "teams.create_membership",
        "teams.delete_membership",
    }
    dataset_view = {
        "datasets.dataset_detail",
        "datasets.dataset_configuration",
        "versions.dataset_versions_view",
        "versions.dataset_version_review",
        "versions.dataset_version_record_diff",
        "versions.dataset_version_field_diff",
        "versions.dataset_version_field_detail",
        "hierarchies.dataset_hierarchy_node_values",
        "records.dataset_records_view",
    }
    dataset_export = {"records.export_dataset_history", "records.export_dataset_records"}
    dataset_export_admin = {
        "datasets.dataset_export_access",
        "datasets.revoke_dataset_export_access",
    }
    dataset_maintain = {
        "datasets.dataset_mapping_source_update",
        "versions.dataset_concept_create",
        "versions.dataset_concept_discard",
        "versions.dataset_version_publish",
        "versions.dataset_version_cancel",
        "datasets.move_dataset_field",
        "hierarchies.dataset_hierarchy_node_create",
        "hierarchies.dataset_hierarchy_node_edit",
        "hierarchies.dataset_hierarchy_node_delete",
        "imports.import_dataset_records",
        "imports.dataset_import_step",
        "imports.select_import_worksheet",
        "imports.review_source_formats",
        "imports.review_import_mapping",
        "imports.preview_record_import",
        "imports.confirm_record_import",
        "imports.cancel_record_import",
        "hierarchies.import_hierarchy_file",
        "hierarchies.hierarchy_import_preview",
        "hierarchies.confirm_hierarchy_import",
        "hierarchies.cancel_hierarchy_import",
        "records.new_dataset_record",
        "records.create_dataset_record",
        "records.edit_dataset_record",
        "records.restore_dataset_record",
        "records.delete_dataset_record",
        "datasets.edit_dataset",
        "datasets.update_dataset",
        "datasets.dataset_delete",
        "datasets.new_dataset_field",
        "datasets.create_dataset_field",
        "datasets.edit_dataset_field",
        "datasets.update_dataset_field",
        "datasets.delete_dataset_field",
    }
    expected = {}
    expected.update(dict.fromkeys(community, "can_access_community"))
    expected["datasets.new_dataset"] = "can_create_dataset"
    expected["datasets.create_dataset"] = "can_create_dataset"
    expected.update(dict.fromkeys(installation_admin, "can_administer_installation"))
    expected.update(dict.fromkeys(access_admin, "can_manage_access"))
    expected.update(dict.fromkeys(dataset_view, "can_view_dataset"))
    expected.update(dict.fromkeys(dataset_export, "can_export_dataset"))
    expected.update(dict.fromkeys(dataset_export_admin, "can_manage_export_access"))
    expected.update(dict.fromkeys(dataset_maintain, "can_maintain_dataset"))
    return expected


def _route_probe_path(rule, *, dataset, team, user, field_key="missing-field"):
    team_id = getattr(team, "id", team)
    values = {
        "access_id": "missing-access",
        "action": "disable",
        "event_id": "missing-event",
        "field_key": field_key,
        "int:step": "1",
        "key": dataset.key,
        "key_id": "missing-api-key",
        "membership_id": "missing-membership",
        "node_id": "missing-node",
        "object_id": "missing-object",
        "record_id": "missing-record",
        "target_id": user.id,
        "team_id": team_id,
        "token": "missing-token",
        "version_id": "1",
        "dataset_id": dataset.id,
    }
    path = rule.rule
    for variable in re.findall(r"<([^>]+)>", path):
        path = path.replace(f"<{variable}>", values[variable])
    return path


@pytest.mark.parametrize(
    "database_backend",
    [
        "sqlite",
        pytest.param(
            "postgresql",
            marks=(
                pytest.mark.postgres,
                pytest.mark.skipif(
                    not os.environ.get("REFRNCR_TEST_POSTGRES_URL"),
                    reason="REFRNCR_TEST_POSTGRES_URL is not configured",
                ),
            ),
        ),
    ],
)
def test_route_authorization_matrix(database_backend, request, monkeypatch):
    services_fixture = request.getfixturevalue(
        "record_services" if database_backend == "sqlite" else "postgresql_record_services"
    )
    _, _, services, admin = services_fixture
    from dataset_helpers import use_admin_export_policy

    use_admin_export_policy(services)
    viewer = services.users.create_user(
        admin.id, "matrix-viewer@example.com", "Viewer", "valid password"
    )
    maintainer = services.users.create_user(
        admin.id, "matrix-maintainer@example.com", "Maintainer", "valid password"
    )
    exporter = services.users.create_user(
        admin.id, "matrix-exporter@example.com", "Exporter", "valid password"
    )
    managed_user = services.users.create_user(
        admin.id, "matrix-managed@example.com", "Managed", "valid password"
    )
    dataset = create_dataset(services, admin, "matrix-dataset")
    owner_team_id = dataset.owner_team_id
    services.memberships.create_membership(admin.id, maintainer.id, owner_team_id)
    services.datasets.grant_export_access(admin.id, dataset.key, "user", exporter.id)
    app = create_flask_app(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        authorization=services.authorization,
        datasets=services.datasets,
        dataset_fields=services.dataset_fields,
        dataset_business_keys=services.dataset_business_keys,
    )
    expectations = _route_matrix_expectations()
    rules = [
        rule
        for rule in app.url_map.iter_rules()
        if rule.endpoint != "static"
        and not getattr(app.view_functions[rule.endpoint], "public_route", False)
    ]
    assert {rule.endpoint for rule in rules} == set(expectations)

    original_has_capability = services.authorization.has_capability
    calls = []

    def traced_has_capability(user_id, capability, resource_id=None, *, uow=None):
        calls.append((user_id, capability, resource_id))
        return original_has_capability(user_id, capability, resource_id, uow=uow)

    monkeypatch.setattr(services.authorization, "has_capability", traced_has_capability)
    users_by_capability = {
        "can_access_community": viewer,
        "can_create_dataset": viewer,
        "can_administer_installation": admin,
        "can_manage_access": admin,
        "can_view_dataset": viewer,
        "can_maintain_dataset": maintainer,
        "can_export_dataset": exporter,
        "can_manage_export_access": admin,
    }
    lower_user = viewer
    denied_for_lower = {
        "can_administer_installation",
        "can_manage_access",
        "can_maintain_dataset",
        "can_export_dataset",
        "can_manage_export_access",
    }
    for rule in rules:
        expected = expectations[rule.endpoint]
        allowed_user = users_by_capability[expected]
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            for actor in (allowed_user, lower_user):
                session_token, login_session = services.sessions.create(actor)
                client = app.test_client()
                client.set_cookie(SESSION_COOKIE, session_token, domain="localhost")
                calls.clear()
                path = _route_probe_path(
                    rule,
                    dataset=dataset,
                    team=owner_team_id,
                    user=managed_user,
                )
                response = getattr(client, method.lower())(
                    path,
                    base_url=HTTPS,
                    data={"csrf_token": login_session.csrf_token},
                )
                assert calls, (
                    f"{method} {rule.rule} bypassed the central capability hook "
                    f"with status {response.status_code}"
                )
                assert calls[0][1] == expected, f"{method} {rule.rule}"
                if actor is lower_user and expected in denied_for_lower:
                    assert response.status_code in {403, 404}, f"{method} {rule.rule}"


def authenticated_csrf(client):
    response = request(client, "get", "/users")
    assert response.status_code == 200
    return csrf_token(response)


def test_browser_error_pages_keep_the_authenticated_shell(interface):
    _, users, authentication, _, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302

    not_found = request(client, "get", "/does-not-exist")
    method_not_allowed = request(client, "get", "/users/not-an-id/email")

    assert not_found.status_code == 404
    assert method_not_allowed.status_code == 405
    for response, code in ((not_found, 404), (method_not_allowed, 405)):
        page = response_text(response)
        assert f"Error {code}" in page
        assert "Back to overview" in page
        assert "Log out" in page

    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    regular = users.create_user(admin.id, "regular@example.com", "Regular", "valid password")
    assert login(client, regular.email, "valid password").status_code == 302
    forbidden = request(client, "get", "/users")

    assert forbidden.status_code == 403
    assert "Error 403" in response_text(forbidden)
    assert "Log out" in response_text(forbidden)


def test_fastapi_hosts_api_and_flask_boundaries_with_shared_authorization():
    (
        _,
        users,
        authentication,
        sessions,
        teams,
        memberships,
        authorization,
        datasets,
        dataset_fields,
        dataset_business_keys,
    ) = configured_services(include_dataset_dependencies=True)
    client = TestClient(
        create_application(
            users,
            authentication,
            sessions,
            teams,
            memberships,
            authorization=authorization,
            datasets=datasets,
            dataset_fields=dataset_fields,
            dataset_business_keys=dataset_business_keys,
        ),
        base_url="https://testserver",
    )

    assert client.get("/api/health").json() == {"status": "ok"}
    assert "refrncr" in client.get("/").text
    assert client.get("/users", follow_redirects=False).status_code == 302
    assert client.get(f"/teams/{uuid.uuid4()}/members", follow_redirects=False).status_code == 302

    setup_token = csrf_token(client.get("/setup"))
    assert (
        client.post(
            "/setup",
            follow_redirects=False,
            data={
                "email": "admin@example.com",
                "display_name": "Administrator",
                "password": ADMIN_PASSWORD,
                "csrf_token": setup_token,
            },
        ).status_code
        == 302
    )
    login_token = csrf_token(client.get("/login"))
    assert (
        client.post(
            "/login",
            follow_redirects=False,
            data={
                "email": "admin@example.com",
                "password": ADMIN_PASSWORD,
                "csrf_token": login_token,
            },
        ).status_code
        == 302
    )
    assert client.get("/users").status_code == 200
    assert client.get("/teams").status_code == 200
    assert "Datasets" in client.get("/").text
    assert client.get("/datasets").status_code == 200


def test_low_level_app_without_dataset_dependencies_has_no_dataset_ui(interface):
    _, _, _, _, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302

    assert "Datasets" not in response_text(request(client, "get", "/"))
    assert request(client, "get", "/datasets").status_code == 404
    assert request(client, "get", "/datasets/new").status_code == 404
    assert request(client, "get", "/datasets/example/fields/new").status_code == 404
    assert request(client, "get", "/datasets/example/fields/field/edit").status_code == 404
    assert request(client, "post", "/datasets/example/fields/field/delete").status_code == 404


def test_anonymous_root_redirects_to_focused_login(interface):
    *_, client = interface
    response = request(client, "get", "/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?")
    page = response_text(request(client, "get", response.headers["Location"]))
    assert "Sign in to your account" in page
    for excluded in ("Overview", "app-shell", "app-sidebar", "<nav", "Sign up", "Forgot password"):
        assert excluded not in page


def test_authenticated_overview_shell_is_permission_aware_and_escapes_actor(interface):
    _, users, authentication, _, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    member = users.create_user(
        admin.id,
        "shell-member@example.com",
        "<em>Shell member</em>",
        "member password",
    )
    assert login(client, member.email, "member password").status_code == 302

    page = response_text(request(client, "get", "/"))

    assert 'href="/" aria-current="page"' in page
    assert 'href="/users"' not in page
    assert 'href="/teams"' not in page
    assert 'action="/logout"' in page
    assert 'name="csrf_token"' in page
    assert "<em>Shell member</em>" not in page
    assert "&lt;em&gt;Shell member&lt;/em&gt;" in page


@pytest.mark.parametrize(
    "included",
    list(
        chain.from_iterable(
            combinations(
                ("authorization", "datasets", "dataset_fields", "dataset_business_keys"), size
            )
            for size in range(1, 4)
        )
    ),
)
def test_low_level_app_rejects_partial_dataset_composition(included):
    (
        _,
        users,
        authentication,
        sessions,
        teams,
        memberships,
        authorization,
        datasets,
        dataset_fields,
        dataset_business_keys,
    ) = configured_services(include_dataset_dependencies=True)
    available = {
        "authorization": authorization,
        "datasets": datasets,
        "dataset_fields": dataset_fields,
        "dataset_business_keys": dataset_business_keys,
    }

    with pytest.raises(ValueError, match="Dataset browser dependencies"):
        create_flask_app(
            users,
            authentication,
            sessions,
            teams,
            memberships,
            **{name: available[name] for name in included},
        )


def test_setup_requires_matching_csrf_and_can_only_complete_once(interface):
    factory, _, _, _, _, _, client = interface
    form = request(client, "get", "/setup")
    token = csrf_token(form)
    csrf_cookie = client.get_cookie(ANONYMOUS_CSRF_COOKIE)
    assert csrf_cookie is not None
    assert csrf_cookie.secure and csrf_cookie.http_only and csrf_cookie.same_site == "Lax"

    setup_data = {
        "email": "admin@example.com",
        "display_name": "Administrator",
        "password": ADMIN_PASSWORD,
    }
    assert request(client, "post", "/setup", data=setup_data).status_code == 403
    assert (
        request(client, "post", "/setup", data={**setup_data, "csrf_token": "invalid"}).status_code
        == 403
    )
    with factory() as session:
        assert session.scalar(select(func.count(User.id))) == 0

    assert (
        request(client, "post", "/setup", data={**setup_data, "csrf_token": token}).status_code
        == 302
    )
    assert client.get_cookie(ANONYMOUS_CSRF_COOKIE) is None
    assert client.get_cookie(SESSION_COOKIE) is None
    with factory() as session:
        admin = session.scalar(select(User))
        event = session.scalar(select(AuditEvent))
        assert admin is not None and admin.role == "admin"
        assert event is not None
        assert (event.object_type, event.object_id, event.action, event.actor_user_id) == (
            "user",
            admin.id,
            "created",
            admin.id,
        )
        assert session.scalar(select(func.count(LoginSession.id))) == 0
    initialized = request(client, "get", "/setup")
    assert initialized.status_code == 302
    assert initialized.headers["Location"].endswith("/login")
    assert "Create administrator" not in response_text(initialized)


def test_setup_get_uses_auth_layout_and_shared_form_components(interface):
    _, _, _, _, _, _, client = interface

    response = request(client, "get", "/setup")
    page = response_text(response)

    assert response.status_code == 200
    assert "<title>Set up administrator · REFRNCR</title>" in page
    assert "<section class='card' aria-labelledby='auth-title'>" in page
    assert (
        "<form class='setup-form' method='post' autocomplete='on' "
        "data-form-type='register'>" in page
    )
    assert "<label for='setup-email'>Email address</label>" in page
    assert "id='setup-email' name='email' type='email'" in page
    assert "autocomplete='email' required" in page
    assert "<label for='setup-name'>Name</label>" in page
    assert "id='setup-name' name='display_name' type='text'" in page
    assert "autocomplete='name' required" in page
    assert "<label for='setup-password'>Password</label>" in page
    assert "id='setup-password' name='password' type='password'" in page
    assert "autocomplete='new-password' data-form-type='password,new' required" in page
    assert "aria-describedby='setup-password-help'" in page
    assert "id='setup-password-help'>Use at least 12 characters.</p>" in page
    assert (
        '<button class="button button-primary" type="submit">Create administrator</button>' in page
    )
    assert "aria-invalid='true'" not in page


def test_setup_validation_renders_accessible_error_without_password_or_partial_write(interface):
    factory, _, _, _, _, _, client = interface
    token = csrf_token(request(client, "get", "/setup"))
    submitted_name = "<strong>Administrator</strong>"
    secret = "too short"

    response = request(
        client,
        "post",
        "/setup",
        data={
            "email": "Admin@Example.com",
            "display_name": submitted_name,
            "password": secret,
            "csrf_token": token,
        },
    )
    page = response_text(response)

    assert response.status_code == 400
    assert "Password must contain at least 12 characters" in page
    assert "id='setup-password-error'" in page
    assert "aria-describedby='setup-password-help setup-password-error'" in page
    assert sum(attrs.get("aria-invalid") == "true" for _, attrs in html_elements(page)) == 1
    assert "value='Admin@Example.com'" in page
    assert "value='&lt;strong&gt;Administrator&lt;/strong&gt;'" in page
    assert submitted_name not in page
    assert secret not in page
    assert "name='password'" in page and "value='too short'" not in page
    with factory() as session:
        assert session.scalar(select(func.count(User.id))) == 0
        assert session.scalar(select(func.count(AuditEvent.id))) == 0
        assert session.scalar(select(func.count(LoginSession.id))) == 0


@pytest.mark.parametrize(
    ("overrides", "error_field", "message"),
    [
        ({"display_name": "   "}, "setup-name", "Display name is required"),
        ({"email": "invalid"}, "setup-email", "A valid email address is required"),
        (
            {"password": "short"},
            "setup-password",
            "Password must contain at least 12 characters",
        ),
    ],
)
def test_setup_validation_marks_only_the_failing_field(interface, overrides, error_field, message):
    _, _, _, _, _, _, client = interface
    token = csrf_token(request(client, "get", "/setup"))
    data = {
        "email": "admin@example.com",
        "display_name": "Administrator",
        "password": ADMIN_PASSWORD,
        "csrf_token": token,
    }

    response = request(client, "post", "/setup", data={**data, **overrides})
    page = response_text(response)

    assert response.status_code == 400
    assert message in page
    assert sum(attrs.get("aria-invalid") == "true" for _, attrs in html_elements(page)) == 1
    assert f"id='{error_field}'" in page
    assert f"{error_field}-error" in page
    assert "value='short'" not in page


def test_superseded_setup_csrf_token_is_rejected_without_admin_creation(interface):
    factory, _, _, _, _, _, client = interface
    superseded = csrf_token(request(client, "get", "/setup"))
    current = csrf_token(request(client, "get", "/setup"))
    assert superseded != current

    response = request(
        client,
        "post",
        "/setup",
        data={
            "email": "admin@example.com",
            "display_name": "Administrator",
            "password": ADMIN_PASSWORD,
            "csrf_token": superseded,
        },
    )

    assert response.status_code == 403
    with factory() as session:
        assert session.scalar(select(func.count(User.id))) == 0
        assert session.scalar(select(func.count(AuditEvent.id))) == 0


def test_login_requires_csrf_uses_secure_cookie_and_hides_authentication_details(interface):
    _, _, _, _, _, _, client = interface
    setup_admin(client)
    csrf_token(request(client, "get", "/login"))

    credentials = {"email": "admin@example.com", "password": ADMIN_PASSWORD}
    assert request(client, "post", "/login", data=credentials).status_code == 403
    assert (
        request(client, "post", "/login", data={**credentials, "csrf_token": "invalid"}).status_code
        == 403
    )
    assert (
        request(client, "post", "/login", data={**credentials, "csrf_token": "é"}).status_code
        == 403
    )

    response = login(client)
    assert response.status_code == 302
    cookie = response.headers["Set-Cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Max-Age=43200" in cookie and "Path=/" in cookie

    for email, password in [
        ("admin@example.com", "wrong password"),
        ("unknown@example.com", "wrong password"),
        ("malformed email", "wrong password"),
    ]:
        response = login(client, email, password)
        assert response.status_code == 401
        body = response_text(response)
        assert "Invalid credentials" in body
        assert f"value='{email}'" in body
        assert password not in body


def test_login_get_uses_auth_layout_and_shared_form_components(interface):
    _, _, _, _, _, _, client = interface
    setup_admin(client)

    response = request(client, "get", "/login")
    page = response_text(response)

    assert response.status_code == 200
    assert "<title>Sign in · REFRNCR</title>" in page
    assert "<section class='card' aria-labelledby='auth-title'>" in page
    assert (
        "<form class='login-form' method='post' action='/login' autocomplete='on' "
        "data-form-type='login' novalidate>" in page
    )
    assert "<label for='login-email'>Email address</label>" in page
    assert "id='login-email' name='email' type='email'" in page
    assert "autocomplete='username' data-form-type='username' required" in page
    assert "<label for='login-password'>Password</label>" in page
    assert "id='login-password' name='password' type='password'" in page
    assert "autocomplete='current-password' data-form-type='password' required" in page
    assert '<button class="button button-primary" type="submit">Sign in</button>' in page
    assert "aria-invalid='true'" not in page
    assert "aria-describedby='login-error'" not in page


def test_invalid_login_uses_generic_accessible_feedback_without_reflection(interface):
    _, _, _, _, _, _, client = interface
    setup_admin(client)
    token = csrf_token(request(client, "get", "/login"))
    submitted_email = "<img src=x onerror=alert(1)>@example.com"

    response = request(
        client,
        "post",
        "/login",
        data={
            "email": submitted_email,
            "password": "incorrect password",
            "csrf_token": token,
        },
    )
    page = response_text(response)

    assert response.status_code == 401
    assert html_tag_texts(page, "div").count("Invalid credentials") == 1
    assert "id='login-error' role='alert'" in page
    elements = html_elements(page)
    assert sum(attrs.get("aria-describedby") == "login-error" for _, attrs in elements) == 2
    assert sum(attrs.get("aria-invalid") == "true" for _, attrs in elements) == 2
    assert submitted_email not in page
    assert "&lt;img" in page
    assert "incorrect password" not in page


def test_superseded_anonymous_csrf_token_is_rejected(interface):
    _, _, _, _, _, _, client = interface
    setup_admin(client)
    superseded = csrf_token(request(client, "get", "/login"))
    current = csrf_token(request(client, "get", "/login"))
    assert superseded != current

    response = request(
        client,
        "post",
        "/login",
        data={
            "email": "admin@example.com",
            "password": ADMIN_PASSWORD,
            "csrf_token": superseded,
        },
    )

    assert response.status_code == 403


def test_login_template_autoescapes_component_feedback(interface):
    _, _, _, _, _, _, client = interface
    with client.application.test_request_context("/login", base_url=HTTPS):
        page = client.application.jinja_env.get_template("pages/auth/login.html").render(
            csrf_token="csrf",
            error="<img src=x onerror=alert(1)>",
            current_actor=None,
            current_csrf=None,
            active_navigation="",
            dataset_ui_enabled=False,
        )

    assert "<img src=x onerror=alert(1)>" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page


def test_successful_login_rotates_and_revokes_existing_session(interface):
    _, _, _, sessions, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    old_token = client.get_cookie(SESSION_COOKIE).value

    assert login(client).status_code == 302
    new_token = client.get_cookie(SESSION_COOKIE).value

    assert old_token != new_token
    assert sessions.resolve(old_token) is None
    assert sessions.resolve(new_token) is not None


def test_unauthenticated_and_non_admin_users_are_rejected(interface):
    _, users, authentication, _, _, _, client = interface
    setup_admin(client)
    assert request(client, "get", "/users").status_code == 302

    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    users.create_user(admin.id, "member@example.com", "Member", "member password")
    assert login(client, "member@example.com", "member password").status_code == 302
    assert request(client, "get", "/users").status_code == 403
    assert request(client, "post", "/users", data={}).status_code == 403


@pytest.mark.parametrize(
    ("path", "data"),
    [
        (
            "/users",
            {"email": "new@example.com", "display_name": "New", "password": "valid password"},
        ),
        ("/users/{target_id}/email", {"email": "changed@example.com"}),
        ("/users/{target_id}/disable", {}),
        ("/users/{target_id}/enable", {}),
        ("/users/{target_id}/delete", {}),
    ],
)
def test_every_user_management_post_rejects_missing_csrf(interface, path, data):
    _, users, authentication, _, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "managed@example.com", "Managed", "valid password")

    response = request(client, "post", path.format(target_id=managed.id), data=data)
    assert response.status_code == 403


def test_logout_requires_csrf_revokes_session_and_deletes_cookie(interface):
    _, _, _, sessions, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    token = client.get_cookie(SESSION_COOKIE).value
    csrf = authenticated_csrf(client)

    assert request(client, "post", "/logout", data={}).status_code == 403
    assert sessions.resolve(token) is not None

    response = request(client, "post", "/logout", data={"csrf_token": csrf})
    assert response.status_code == 302
    assert sessions.resolve(token) is None
    assert client.get_cookie(SESSION_COOKIE) is None
    deleted_cookie = response.headers["Set-Cookie"]
    assert "Expires=Thu, 01 Jan 1970" in deleted_cookie and "Max-Age=0" in deleted_cookie


def test_disabled_and_deleted_users_cannot_login_or_reuse_sessions(interface):
    _, users, authentication, sessions, _, _, client = interface
    setup_admin(client)
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "managed@example.com", "Managed", "member password")

    assert login(client, managed.email, "member password").status_code == 302
    disabled_token = client.get_cookie(SESSION_COOKIE).value
    users.disable_user(admin.id, managed.id)
    assert sessions.resolve(disabled_token) is None
    disabled_response = request(client, "get", "/users")
    assert disabled_response.status_code == 302
    assert "Max-Age=0" in disabled_response.headers["Set-Cookie"]
    assert login(client, managed.email, "member password").status_code == 401

    users.enable_user(admin.id, managed.id)
    assert login(client, managed.email, "member password").status_code == 302
    deleted_token = client.get_cookie(SESSION_COOKIE).value
    users.delete_user(admin.id, managed.id)
    assert sessions.resolve(deleted_token) is None
    deleted_response = request(client, "get", "/users")
    assert deleted_response.status_code == 302
    assert "Max-Age=0" in deleted_response.headers["Set-Cookie"]
    assert login(client, managed.email, "member password").status_code == 401


def test_user_list_escapes_user_controlled_values(interface):
    _, users, authentication, _, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    users.create_user(
        admin.id,
        "<script>alert(1)</script>@example.com",
        "<img src=x onerror=alert(1)>",
        "valid password",
    )

    body = response_text(request(client, "get", "/users"))
    assert "<script>alert(1)</script>" not in body
    assert "<img src=x onerror=alert(1)>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;@example.com" in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body


def test_user_list_uses_management_layout_and_add_user_panel(interface):
    _, _, _, _, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302

    body = response_text(request(client, "get", "/users"))

    assert '<h1 class="page-title">Users</h1>' in body
    assert "data-list-search" in body
    assert "<caption class='visually-hidden'>Users</caption>" in body
    assert "class='table-cell-wrap' scope='col'>Email address</th>" in body
    assert not re.search(r"<tr\b[^>]*(?:tabindex=|role=['\"]button)", body)
    assert "aria-label='User filters'" not in body
    assert "data-dialog-open='add-user-panel'" in body
    assert "id='add-user-panel' data-side-panel" in body
    assert "data-dialog-open='user-panel-" in body
    assert "data-panel-entity='user'" in body
    assert "data-panel-mode='edit'>Edit user" in body
    assert "data-panel-mode='delete'>Delete user" in body
    assert "data-panel-view='edit' hidden autocomplete='off' data-form-type='other'" in body
    assert "This deletion cannot be undone." in body
    assert "action='/users'" in body
    assert "name='role'" in body
    assert "data-modal-side-panel" in body


def test_users_mobile_table_preserves_identity_and_view_action(interface):
    _, users, authentication, _, _, _, client = interface
    setup_admin(client)
    login(client)
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(
        admin.id,
        "long-unbroken-address-for-mobile-testing@example.com",
        "LongUnbrokenDisplayNameForMobileTesting",
        "valid password",
    )
    body = response_text(request(client, "get", "/users"))
    table = re.search(
        r"<table class='user-management-table' role='table'>(.*?)</table>", body, re.S
    ).group(1)
    headers = re.findall(r"<th\b([^>]*)>(.*?)</th>", table, re.S)
    assert len(headers) == 5
    expected_classes = [
        "table-cell-wrap",
        "table-cell-wrap",
        "table-cell-wrap",
        "table-column-secondary",
        "table-column-secondary",
    ]
    assert [re.search(r"class='([^']+)'", attrs).group(1) for attrs, _ in headers] == (
        expected_classes
    )
    assert [text for _, text in headers] == ["Name", "Email address", "Teams", "Role", "Status"]
    header_ids = [re.search(r"id='([^']+)'", attrs).group(1) for attrs, _ in headers]
    assert all("role='columnheader'" in attrs and "scope='col'" in attrs for attrs, _ in headers)
    assert "<caption class='visually-hidden'>Users</caption>" in table
    assert "<thead role='rowgroup'><tr role='row'>" in table
    rows = re.findall(r"<tbody role='rowgroup'>(.*?)</tbody>", table, re.S)[0]
    row_elements = html_elements(rows)
    assert sum(attrs.get("role") == "row" for _, attrs in row_elements) == sum(
        tag == "tr" for tag, _ in row_elements
    )
    expected_cell_classes = expected_classes.copy()
    expected_cell_classes[2] = "table-cell-wrap relationship-cell"
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", rows, re.S):
        cells = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S)
        assert [re.search(r"class='([^']+)'", attrs).group(1) for attrs, _ in cells] == (
            expected_cell_classes
        )
        assert [re.search(r"headers='([^']+)'", attrs).group(1) for attrs, _ in cells] == header_ids
        assert all("role='cell'" in attrs for attrs, _ in cells)
        assert f"<span class='row-action-label'>{managed.display_name}</span>" in rows
        assert managed.email in rows
        action = cells[0][1]
        assert "type='button'" in action
        assert "data-dialog-open='user-panel-" in action
        assert "aria-label='View user " in action
        assert "data-row-primary" in action
        assert ">View</button>" not in row
        assert "disabled" not in action
    assert f"aria-label='View user {managed.display_name} ({managed.id})'" in table


@pytest.mark.parametrize(
    ("path", "title"), [("/users", "Users"), ("/teams", "Teams"), ("members", "Teams")]
)
def test_settings_layout_navigation_and_context(interface, path, title):
    _, _, authentication, _, teams, _, client = interface
    setup_admin(client)
    login(client)
    if path == "members":
        admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
        team = teams.create_team(admin.id, "Settings team")
        path = f"/teams/{team.id}/members"
    response = request(client, "get", path)
    assert response.status_code == 200
    body = response_text(response)
    assert 'class="app-shell settings-shell" data-responsive-shell' in body
    assert 'class="settings-sidebar" data-navigation' in body
    assert 'class="settings-topbar settings-header"' in body
    assert 'class="settings-title"' in body
    assert 'class="app-sidebar"' not in body
    assert 'id="primary-navigation"' not in body
    assert 'class="account-context"' not in body
    assert ">Overview</a>" not in body
    assert ">Datasets</a>" not in body
    secondary = re.search(r'<nav id="settings-navigation".*?</nav>', body, re.S).group()
    assert 'aria-label="Settings navigation"' in secondary
    assert 'class="primary-navigation"' in secondary
    assert "Access management" in secondary
    assert re.findall(r">(Users|Teams)</a>", secondary) == ["Users", "Teams"]
    secondary_elements = html_elements(secondary)
    assert sum(tag == "a" for tag, _ in secondary_elements) == 6
    assert re.search(
        r'href="/settings/api-keys" data-no-view-transition>.*?</svg>API keys</a>', secondary, re.S
    )
    assert re.search(r'href="/settings/audit-log">.*?</svg>Audit log</a>', secondary, re.S)
    assert re.search(rf'aria-current="page">.*?</svg>{title}</a>', secondary, re.S)
    assert sum(attrs.get("aria-current") == "page" for _, attrs in secondary_elements) == 1
    assert sum(attrs.get("aria-hidden") == "true" for _, attrs in secondary_elements) == 6
    assert re.search(
        r'<a class="settings-back" href="/">.*?</svg>Back to app</a>',
        body,
        re.S,
    )
    assert f"<title>{title}</title>" in body
    assert f'<h1 class="page-title">{title}</h1>' in body
    assert sum(tag == "h1" for tag, _ in html_elements(body)) == 1
    assert 'class="breadcrumbs"' not in body
    assert 'aria-expanded="false" aria-controls="settings-sidebar" data-nav-open' in body
    assert 'aria-label="Expand navigation"' in body
    assert '<main id="content" class="app-main settings-content" tabindex="-1">' in body
    assert (
        sum("flash-region" in attrs.get("class", "").split() for _, attrs in html_elements(body))
        == 1
    )
    assert ">Appearance</a>" in secondary
    assert ">Personal</p>" in secondary


def test_settings_visibility_and_direct_access_follow_authenticated_role(interface):
    _, users, authentication, _, _, _, client = interface
    setup_admin(client)
    login(client)
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    regular = users.create_user(admin.id, "regular@example.com", "Regular", "valid password")
    body = response_text(request(client, "get", "/"))
    assert ">Settings</a>" in body
    assert "settings-shell" not in body
    assert 'id="primary-navigation"' in body
    account = re.search(r'<div class="account-context">.*?</div>', body, re.S).group()
    assert account.index('class="account-name"') < account.index('class="account-role"')
    assert account.index('class="account-role"') < account.index(">Settings</a>")
    assert account.index(">Settings</a>") < account.index(">Log out</button>")
    login(client, regular.email, "valid password")
    body = response_text(request(client, "get", "/"))
    assert ">Settings</a>" in body
    assert re.search(r'href="/settings/appearance">.*?</svg>Settings</a>', body, re.S)
    appearance = request(client, "get", "/settings/appearance")
    assert appearance.status_code == 200
    appearance_body = response_text(appearance)
    assert re.search(r'href="/settings/appearance"[^>]*aria-current="page"', appearance_body)
    assert ">Appearance</a>" in appearance_body
    assert ">Users</a>" not in body
    assert ">Teams</a>" not in body
    assert request(client, "get", "/teams").status_code == 200
    assert request(client, "post", "/teams", data={}).status_code == 403
    for path in ("/users", "/teams/missing/members"):
        assert request(client, "get", path).status_code == 403
        assert request(client, "post", path, data={}).status_code == 403


def test_appearance_settings_requires_authentication(interface):
    _, _, _, _, _, _, client = interface
    response = request(client, "get", "/settings/appearance")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=/settings/appearance")


def test_expected_user_error_does_not_expose_sensitive_values(interface):
    _, _, _, _, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    csrf = authenticated_csrf(client)
    secret_password = "do not expose this password"

    response = request(
        client,
        "post",
        "/users",
        data={
            "email": "admin@example.com",
            "display_name": "Duplicate",
            "password": secret_password,
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 409
    body = response_text(response)
    assert secret_password not in body
    assert "password_hash" not in body
    assert SESSION_COOKIE not in body


@pytest.mark.parametrize(
    "overrides",
    [
        {"email": "malformed email"},
        {"display_name": "   "},
        {"role": "owner"},
    ],
)
def test_create_user_validation_errors_are_controlled(interface, overrides):
    factory, _, _, _, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    csrf = authenticated_csrf(client)
    secret_password = "validation secret"
    data = {
        "email": "new@example.com",
        "display_name": "New User",
        "password": secret_password,
        "role": "user",
        "csrf_token": csrf,
        **overrides,
    }

    response = request(client, "post", "/users", data=data)

    assert response.status_code == 400
    assert secret_password not in response_text(response)
    with factory() as session:
        assert session.scalar(select(func.count(User.id))) == 1


def test_change_email_validation_error_is_controlled(interface):
    _, users, authentication, _, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "managed@example.com", "Managed", "valid password")
    csrf = authenticated_csrf(client)

    malformed_email = '<script>alert("bad")</script>'
    response = request(
        client,
        "post",
        f"/users/{managed.id}/email",
        data={"email": malformed_email, "csrf_token": csrf},
    )

    assert response.status_code == 400
    page = response_text(response)
    assert "value='&lt;script&gt;alert(&#34;bad&#34;)&lt;/script&gt;'" in page
    assert malformed_email not in page
    error = re.search(r"<div class='alert alert-error'[^>]*>(.*?)</div>", page, re.S)
    assert error is not None and "bad" not in error.group(1)
    assert authentication.authenticate("managed@example.com", "valid password").id == managed.id


def test_admin_creates_renames_lists_and_deletes_team(interface):
    factory, _, _, _, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    csrf = authenticated_csrf(client)

    response = request(
        client,
        "post",
        "/teams",
        data={"name": " Platform ", "csrf_token": csrf},
    )
    assert response.status_code == 302
    with factory() as session:
        team = session.scalar(select(Team))
        assert team.name == "Platform"

    body = response_text(request(client, "get", "/teams"))
    assert "Platform" in body
    assert '<h1 class="page-title">Teams</h1>' in body
    assert "data-list-search" in body
    assert "<caption class='visually-hidden'>Teams</caption>" in body
    assert not re.search(r"<tr\b[^>]*(?:tabindex=|role=['\"]button)", body)
    assert "aria-label='Team filters'" not in body
    assert "data-dialog-open='add-team-panel'" in body
    table = re.search(
        r"<table class='relationship-table' role='table'>(.*?)</table>", body, re.S
    ).group(1)
    assert sum("data-row-primary" in attrs for _, attrs in html_elements(table)) == 1
    assert "data-row-action" in table
    assert "Actions" not in table
    assert ">View</a>" not in table
    assert "row-chevron" not in table  # Rows read as clickable without a caret.
    assert f"data-row-primary href='/teams?open_team={team.id}'" in table
    body = response_text(request(client, "get", f"/teams?open_team={team.id}"))
    assert f"id='team-panel-{team.id}'" in body
    assert "data-modal-side-panel" in body
    assert "data-panel-entity='team'" in body
    assert "data-panel-mode='edit'>Edit team" in body
    assert "data-panel-mode='delete'>Delete team" in body
    assert f"action='/teams/{team.id}/rename'" in body
    assert f"action='/teams/{team.id}/delete'" in body
    assert "<h3>Team members</h3>" in body
    assert f"action='/teams/{team.id}/members'" in body
    response = request(
        client,
        "post",
        f"/teams/{team.id}/rename",
        data={"name": "Delivery", "csrf_token": csrf},
    )
    assert response.status_code == 302
    assert "Delivery" in response_text(request(client, "get", "/teams"))

    response = request(
        client,
        "post",
        f"/teams/{team.id}/delete",
        data={"csrf_token": csrf},
    )
    assert response.status_code == 302
    with factory() as session:
        assert session.get(Team, team.id) is None


def test_team_list_only_loads_memberships_for_the_open_team(interface, monkeypatch):
    _, _, authentication, _, teams, memberships, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    first = teams.create_team(admin.id, "First")
    teams.create_team(admin.id, "Second")
    member_calls = []
    eligible_calls = []
    original_members = memberships.list_team_members
    original_eligible = memberships.list_eligible_users

    def list_members(actor_id, team_id):
        member_calls.append(team_id)
        return original_members(actor_id, team_id)

    def list_eligible(actor_id, team_id):
        eligible_calls.append(team_id)
        return original_eligible(actor_id, team_id)

    monkeypatch.setattr(memberships, "list_team_members", list_members)
    monkeypatch.setattr(memberships, "list_eligible_users", list_eligible)

    assert request(client, "get", "/teams").status_code == 200
    assert member_calls == [] and eligible_calls == []
    assert request(client, "get", f"/teams?open_team={first.id}").status_code == 200
    assert member_calls == [first.id]
    assert eligible_calls == [first.id]


def test_team_interface_rejects_unauthenticated_and_regular_users(interface):
    _, users, authentication, _, teams, _, client = interface
    setup_admin(client)
    assert request(client, "get", "/teams").status_code == 302
    assert request(client, "post", "/teams", data={"name": "Forbidden"}).status_code == 302

    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    users.create_user(admin.id, "member@example.com", "Member", "member password")
    assert login(client, "member@example.com", "member password").status_code == 302
    assert request(client, "get", "/teams").status_code == 200
    assert request(client, "post", "/teams", data={"name": "Forbidden"}).status_code == 403
    assert teams.list_teams(admin.id) == []


@pytest.mark.parametrize("token", [None, "invalid", "é"])
@pytest.mark.parametrize("operation", ["create", "rename", "delete"])
def test_every_team_state_change_requires_csrf(interface, operation, token):
    _, users, authentication, _, teams, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    team = teams.create_team(admin.id, "Existing")
    routes = {
        "create": ("/teams", {"name": "New"}),
        "rename": (f"/teams/{team.id}/rename", {"name": "Renamed"}),
        "delete": (f"/teams/{team.id}/delete", {}),
    }
    path, data = routes[operation]
    if token is not None:
        data["csrf_token"] = token

    response = request(client, "post", path, data=data)

    assert response.status_code == 403
    assert [stored.name for stored in teams.list_teams(admin.id)] == ["Existing"]


def test_team_interface_escapes_names_and_controls_validation_errors(interface):
    _, users, authentication, _, teams, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    teams.create_team(admin.id, "<script>alert(1)</script>")
    csrf = authenticated_csrf(client)

    body = response_text(request(client, "get", "/teams"))
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body

    response = request(
        client,
        "post",
        "/teams",
        data={"name": " ", "csrf_token": csrf},
    )
    assert response.status_code == 400
    assert "<script>" not in response_text(response)

    response = request(
        client,
        "post",
        "/teams",
        data={"name": "<SCRIPT>ALERT(1)</SCRIPT>", "csrf_token": csrf},
    )
    assert response.status_code == 409
    assert "<SCRIPT>" not in response_text(response)


def test_admin_views_empty_team_and_adds_and_removes_member(interface):
    factory, users, authentication, _, teams, memberships, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "member@example.com", "Member", "valid password")
    team = teams.create_team(admin.id, "Platform")
    csrf = authenticated_csrf(client)

    response = request(client, "get", f"/teams/{team.id}/members")
    assert response.status_code == 200
    assert "This team has no members." in response_text(response)

    response = request(
        client,
        "post",
        f"/teams/{team.id}/members",
        data={"user_id": managed.id, "csrf_token": csrf},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/teams?open_team={team.id}")
    with factory() as session:
        membership = session.scalar(select(Membership))
    body = response_text(request(client, "get", f"/teams/{team.id}/members"))
    assert "Member" in body and "member@example.com" in body

    response = request(
        client,
        "post",
        f"/teams/{team.id}/members/{membership.id}/delete",
        data={"csrf_token": csrf},
    )
    assert response.status_code == 302
    assert memberships.list_team_members(admin.id, team.id) == []


@pytest.mark.parametrize("token", [None, "invalid", "é"])
@pytest.mark.parametrize("operation", ["create", "delete"])
def test_every_membership_state_change_requires_csrf(interface, operation, token):
    _, users, authentication, _, teams, memberships, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "member@example.com", "Member", "valid password")
    team = teams.create_team(admin.id, "Platform")
    membership = memberships.create_membership(admin.id, managed.id, team.id)
    if operation == "create":
        second = users.create_user(admin.id, "second@example.com", "Second", "valid password")
        path = f"/teams/{team.id}/members"
        data = {"user_id": second.id}
    else:
        path = f"/teams/{team.id}/members/{membership.id}/delete"
        data = {}
    if token is not None:
        data["csrf_token"] = token

    response = request(client, "post", path, data=data)

    assert response.status_code == 403
    assert len(memberships.list_team_members(admin.id, team.id)) == 1


def test_membership_interface_authorization_uses_service_for_all_actor_states(interface):
    _, users, authentication, _, teams, memberships, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "member@example.com", "Member", "valid password")
    team = teams.create_team(admin.id, "Platform")

    assert request(client, "get", f"/teams/{team.id}/members").status_code == 200
    request(client, "post", "/logout", data={"csrf_token": authenticated_csrf(client)})
    assert request(client, "get", f"/teams/{team.id}/members").status_code == 302

    actors = [
        users.create_user(admin.id, "regular@example.com", "Regular", "valid password"),
        users.create_user(
            admin.id, "disabled@example.com", "Disabled", "valid password", role="admin"
        ),
        users.create_user(
            admin.id, "deleted@example.com", "Deleted", "valid password", role="admin"
        ),
    ]
    assert login(client, "regular@example.com", "valid password").status_code == 302
    assert request(client, "get", f"/teams/{team.id}/members").status_code == 403
    assert login(client, "disabled@example.com", "valid password").status_code == 302
    users.disable_user(admin.id, actors[1].id)
    assert request(client, "get", f"/teams/{team.id}/members").status_code == 302
    assert login(client, "deleted@example.com", "valid password").status_code == 302
    users.delete_user(admin.id, actors[2].id)
    assert request(client, "get", f"/teams/{team.id}/members").status_code == 302
    for actor in actors:
        with pytest.raises(AuthorizationError):
            memberships.create_membership(actor.id, managed.id, team.id)
        with pytest.raises(AuthorizationError):
            memberships.delete_membership(actor.id, team.id, str(uuid.uuid4()))


def test_membership_interface_controls_invalid_targets_duplicates_and_lifecycle(interface):
    _, users, authentication, _, teams, memberships, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "member@example.com", "Member", "valid password")
    disabled = users.create_user(admin.id, "disabled@example.com", "Disabled", "valid password")
    deleted = users.create_user(admin.id, "deleted@example.com", "Deleted", "valid password")
    team = teams.create_team(admin.id, "Platform")
    membership = memberships.create_membership(admin.id, managed.id, team.id)
    disabled_membership = memberships.create_membership(admin.id, disabled.id, team.id)
    deleted_membership = memberships.create_membership(admin.id, deleted.id, team.id)
    users.disable_user(admin.id, disabled.id)
    users.delete_user(admin.id, deleted.id)
    csrf = authenticated_csrf(client)

    cases = [
        (f"/teams/{team.id}/members", {"user_id": managed.id, "csrf_token": csrf}, 409),
        (
            f"/teams/{team.id}/members",
            {"user_id": str(uuid.uuid4()), "csrf_token": csrf},
            404,
        ),
        (f"/teams/{team.id}/members", {"user_id": disabled.id, "csrf_token": csrf}, 400),
        (f"/teams/{team.id}/members", {"user_id": deleted.id, "csrf_token": csrf}, 400),
        (
            f"/teams/{uuid.uuid4()}/members",
            {"user_id": managed.id, "csrf_token": csrf},
            404,
        ),
        (
            f"/teams/{team.id}/members/{uuid.uuid4()}/delete",
            {"csrf_token": csrf},
            404,
        ),
    ]
    for path, data, expected_status in cases:
        response = request(client, "post", path, data=data)
        assert response.status_code == expected_status
        assert "password_hash" not in response_text(response)
        assert SESSION_COOKIE not in response_text(response)

    for existing in [disabled_membership, deleted_membership, membership]:
        response = request(
            client,
            "post",
            f"/teams/{team.id}/members/{existing.id}/delete",
            data={"csrf_token": csrf},
        )
        assert response.status_code == 302


def test_membership_interface_escapes_user_values(interface):
    _, users, authentication, _, teams, memberships, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = authentication.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(
        admin.id,
        "<script>alert(1)</script>@example.com",
        "<img src=x onerror=alert(1)>",
        "valid password",
    )
    team = teams.create_team(admin.id, "Platform")
    memberships.create_membership(admin.id, managed.id, team.id)

    body = response_text(request(client, "get", f"/teams/{team.id}/members"))
    assert "<script>alert(1)</script>" not in body
    assert "<img src=x onerror=alert(1)>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;@example.com" in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body


@pytest.mark.parametrize("path", ["/", "/users", "/teams", "/teams/missing/members"])
def test_anonymous_browser_redirect_preserves_only_path(interface, path):
    from urllib.parse import parse_qs, urlsplit

    *_, client = interface
    response = request(client, "get", path + "?password=never-forward-this")
    assert response.status_code == 302
    location = urlsplit(response.headers["Location"])
    assert location.path == "/login"
    assert parse_qs(location.query) == {"next": [path]}
    page = response_text(request(client, "get", response.headers["Location"]))
    assert "never-forward-this" not in page
    assert "Overview" not in page
    assert "app-shell" not in page


@pytest.mark.parametrize(
    ("fields", "invalid"),
    [
        ({}, ["email", "password"]),
        ({"email": "admin@example.com"}, ["password"]),
        ({"password": "never-echo-this"}, ["email"]),
        ({"email": "   ", "password": "never-echo-this"}, ["email"]),
    ],
)
def test_login_required_fields_have_associated_errors(interface, fields, invalid):
    *_, client = interface
    token = csrf_token(request(client, "get", "/login"))
    response = request(client, "post", "/login", data={**fields, "csrf_token": token})
    page = response_text(response)
    assert response.status_code == 400
    for field in invalid:
        assert f"aria-describedby='login-{field}-error'" in page
        assert f"id='login-{field}-error'" in page
    assert sum(attrs.get("aria-invalid") == "true" for _, attrs in html_elements(page)) == len(
        invalid
    )
    assert "tabindex='-1' data-error-focus" in page
    assert "never-echo-this" not in page
    if fields.get("email") == "admin@example.com":
        assert "value='admin@example.com'" in page


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/users",
        "https://localhost/users",
        "//evil.example",
        "///evil.example",
        "users",
        "javascript:alert(1)",
        "/\\evil.example",
        "/%2fevil.example",
        "/%252fevil.example",
        "/%5cevil.example",
        "/users%0d%0aLocation:evil",
        "/./users",
        "/users/../login",
        "/users?password=secret",
        "/users#secret",
        "/login",
        "/logout",
        "/setup",
        " /users",
    ],
)
def test_login_rejects_unsafe_return_targets_on_get_and_post(interface, target):
    *_, client = interface
    setup_admin(client)
    response = request(client, "get", "/login", query_string={"next": target})
    assert "name='next' value='/'" in response_text(response)
    response = request(
        client,
        "post",
        "/login",
        data={
            "email": "admin@example.com",
            "password": ADMIN_PASSWORD,
            "csrf_token": csrf_token(response),
            "next": target,
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert ADMIN_PASSWORD not in response.headers["Location"]


@pytest.mark.parametrize("target", ["/", "/settings/audit-log", "/datasets/records/import"])
def test_safe_nested_return_target_is_kept_exactly(interface, target):
    *_, client = interface
    setup_admin(client)
    form = request(client, "get", "/login", query_string={"next": target})
    assert f"name='next' value='{target}'" in response_text(form)
    response = request(
        client,
        "post",
        "/login",
        data={
            "email": "admin@example.com",
            "password": ADMIN_PASSWORD,
            "csrf_token": csrf_token(form),
            "next": target,
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == target


def test_safe_return_target_survives_failure_and_success(interface):
    *_, client = interface
    setup_admin(client)
    form = request(client, "get", "/login", query_string={"next": "/teams"})
    response = request(
        client,
        "post",
        "/login",
        data={
            "email": "admin@example.com",
            "password": "wrong password",
            "csrf_token": csrf_token(form),
            "next": "/teams",
        },
    )
    assert response.status_code == 401
    assert "name='next' value='/teams'" in response_text(response)
    response = request(
        client,
        "post",
        "/login",
        data={
            "email": "admin@example.com",
            "password": ADMIN_PASSWORD,
            "csrf_token": csrf_token(response),
            "next": "/teams",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/teams"
    assert request(client, "get", "/teams").status_code == 200


def test_login_password_toggle_is_named_and_cannot_submit(interface):
    from html.parser import HTMLParser

    class Buttons(HTMLParser):
        def __init__(self):
            super().__init__()
            self.buttons = []

        def handle_starttag(self, tag, attrs):
            if tag == "button":
                self.buttons.append(dict(attrs))

    *_, client = interface
    page = response_text(request(client, "get", "/login"))
    parser = Buttons()
    parser.feed(page)
    (toggle,) = [button for button in parser.buttons if "data-password-toggle" in button]
    assert toggle["type"] == "button"
    assert toggle["aria-controls"] == "login-password"
    assert "Show password</button>" in page
    assert "images/refrncr-wordmark-primary.svg" in page
    assert 'class="auth-wordmark"' in page and 'alt="" width="200" height="52"' in page
    assert 'class="brand-name"' not in page
    assert 'aria-label="REFRNCR home"' in page
    assert "Sign in to your account" in page
    assert "Sign up" not in page and "Forgot password" not in page

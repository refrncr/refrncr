import re
import uuid
from itertools import chain, combinations
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.pool import StaticPool

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


def authenticated_csrf(client):
    response = request(client, "get", "/users")
    assert response.status_code == 200
    return csrf_token(response)


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
    assert "<form class='setup-form' method='post'>" in page
    assert "<label for='setup-email'>Email address</label>" in page
    assert "id='setup-email' name='email' type='email'" in page
    assert "autocomplete='email' required" in page
    assert "<label for='setup-name'>Name</label>" in page
    assert "id='setup-name' name='display_name' type='text'" in page
    assert "autocomplete='name' required" in page
    assert "<label for='setup-password'>Password</label>" in page
    assert "id='setup-password' name='password' type='password'" in page
    assert "autocomplete='new-password' required" in page
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
    assert page.count("aria-invalid='true'") == 1
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
    assert page.count("aria-invalid='true'") == 1
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
    assert "<form class='login-form' method='post' action='/login' novalidate>" in page
    assert "<label for='login-email'>Email address</label>" in page
    assert "id='login-email' name='email' type='email'" in page
    assert "autocomplete='username' required" in page
    assert "<label for='login-password'>Password</label>" in page
    assert "id='login-password' name='password' type='password'" in page
    assert "autocomplete='current-password' required" in page
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
    assert page.count("Invalid credentials") == 1
    assert "id='login-error' role='alert'" in page
    assert page.count("aria-describedby='login-error'") == 2
    assert page.count("aria-invalid='true'") == 2
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
    assert "data-panel-view='edit' hidden method='post' action='/users/" in body
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
    assert len(headers) == 4
    expected_classes = [
        "table-cell-wrap",
        "table-cell-wrap",
        "table-column-secondary",
        "table-column-secondary",
    ]
    assert [re.search(r"class='([^']+)'", attrs).group(1) for attrs, _ in headers] == (
        expected_classes
    )
    assert [text for _, text in headers[:4]] == ["Name", "Email address", "Role", "Status"]
    header_ids = [re.search(r"id='([^']+)'", attrs).group(1) for attrs, _ in headers]
    assert all("role='columnheader'" in attrs and "scope='col'" in attrs for attrs, _ in headers)
    assert "<caption class='visually-hidden'>Users</caption>" in table
    assert "<thead role='rowgroup'><tr role='row'>" in table
    rows = re.findall(r"<tbody role='rowgroup'>(.*?)</tbody>", table, re.S)[0]
    assert rows.count("role='row'") == rows.count("<tr ")
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", rows, re.S):
        cells = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S)
        assert [re.search(r"class='([^']+)'", attrs).group(1) for attrs, _ in cells] == (
            expected_classes
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

    css = response_text(request(client, "get", "/static/css/components/tables.css"))
    breakpoint = "@media (max-width: 767px)"
    desktop, mobile = css.split(breakpoint)
    assert "user-management-table" not in desktop
    rules = {
        selector.strip(): declarations
        for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", mobile)
    }
    hidden = [selector for selector, rule in rules.items() if "display: none" in rule]
    assert hidden == [".user-management-table .table-column-secondary"]
    row_rule = rules[".user-management-table tbody tr"]
    assert "display: grid;" in row_rule
    assert "grid-template-columns: minmax(0, 1fr);" in row_rule
    assert "border-bottom: 1px solid var(--color-border-subtle);" in row_rule
    assert "grid-column: 1;" in rules[".user-management-table .table-cell-wrap"]
    header_rule = rules[".user-management-table thead"]
    assert "position: absolute;" in header_rule
    assert "clip: rect(0 0 0 0);" in header_rule
    assert "display: none" not in header_rule
    assert "table-column-action" not in table
    assert "overflow-wrap: anywhere;" in rules[".user-management-table .table-cell-wrap"]

    panel_css = response_text(request(client, "get", "/static/css/components/dialog.css"))
    mobile_panel = panel_css.split(breakpoint)[1].split("/* Shared")[0]
    assert "grid-template-columns: minmax(0, 1fr) var(--control-height);" in mobile_panel
    assert "width: var(--control-height); justify-self: end; align-self: start;" in mobile_panel


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
    assert secondary.count("<a ") == 3
    assert re.search(r'href="/settings/audit-log">.*?</svg>Audit log</a>', secondary, re.S)
    assert re.search(rf'aria-current="page">.*?</svg>{title}</a>', secondary, re.S)
    assert secondary.count('aria-current="page"') == 1
    assert secondary.count('aria-hidden="true"') == 3
    assert re.search(
        r'<a class="settings-back" href="/">.*?</svg>Back to app</a>',
        body,
        re.S,
    )
    assert f"<title>{title}</title>" in body
    assert f'<h1 class="page-title">{title}</h1>' in body
    assert body.count("<h1") == 1
    assert 'class="breadcrumbs"' not in body
    assert 'aria-expanded="false" aria-controls="settings-sidebar" data-nav-open' in body
    assert 'aria-label="Expand navigation"' in body
    assert '<main id="content" class="app-main settings-content" tabindex="-1">' in body
    assert body.count('class="flash-region"') == 1
    shell_styles = response_text(request(client, "get", "/static/css/layouts/app-shell.css"))
    assert re.search(r"\.primary-navigation\s*{[^}]*flex-direction: column;", shell_styles)
    for label in (
        "Personal",
        "Appearance",
        "Tasks",
        "Cases",
        "Theme",
        "Developer",
        "Billing",
        "Notifications",
        "Language",
    ):
        assert label not in secondary


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
    assert ">Settings</a>" not in body
    assert ">Users</a>" not in body
    assert ">Teams</a>" not in body
    assert request(client, "get", "/teams").status_code == 200
    assert request(client, "post", "/teams", data={}).status_code == 403
    for path in ("/users", "/teams/missing/members"):
        assert request(client, "get", path).status_code == 403
        assert request(client, "post", path, data={}).status_code == 403


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
    assert response.status_code == 400
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

    response = request(
        client,
        "post",
        f"/users/{managed.id}/email",
        data={"email": "malformed email", "csrf_token": csrf},
    )

    assert response.status_code == 400
    assert "malformed email" not in response_text(response)
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
    table = re.search(r"<table>(.*?)</table>", body, re.S).group(1)
    assert table.count("data-row-primary") == 1
    assert "data-row-action" in table
    assert "Actions" not in table
    assert ">View</a>" not in table
    assert "class='row-chevron'" in table
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


def test_management_routes_use_file_templates_without_inline_markup():
    web_source = (Path(__file__).parents[1] / "src/refrncr/web.py").read_text()
    management_source = web_source.split('    @app.get("/users")', 1)[1].split(
        "    @app.errorhandler(UserManagementError)", 1
    )[0]

    for template in (
        '"pages/users/list.html"',
        '"pages/teams/list.html"',
        '"pages/teams/members.html"',
    ):
        assert template in management_source
    assert "render_template_string" not in management_source
    assert '"<dialog' not in management_source
    assert '"<form' not in management_source


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
    assert response.status_code == 400
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

    for path, data in [
        (f"/teams/{team.id}/members", {"user_id": managed.id, "csrf_token": csrf}),
        (
            f"/teams/{team.id}/members",
            {"user_id": str(uuid.uuid4()), "csrf_token": csrf},
        ),
        (f"/teams/{team.id}/members", {"user_id": disabled.id, "csrf_token": csrf}),
        (f"/teams/{team.id}/members", {"user_id": deleted.id, "csrf_token": csrf}),
        (
            f"/teams/{uuid.uuid4()}/members",
            {"user_id": managed.id, "csrf_token": csrf},
        ),
        (
            f"/teams/{team.id}/members/{uuid.uuid4()}/delete",
            {"csrf_token": csrf},
        ),
    ]:
        response = request(client, "post", path, data=data)
        assert response.status_code == 400
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
    assert page.count("aria-invalid='true'") == len(invalid)
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
    assert "images/logo-horizontal.svg" in page
    assert 'alt="REFRNCR"' in page
    assert "Sign in to your account" in page
    assert "Sign up" not in page and "Forgot password" not in page

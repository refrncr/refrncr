import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.pool import StaticPool

from refrncr.app import create_application
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import User
from refrncr.services import SessionService, UserService
from refrncr.web import ANONYMOUS_CSRF_COOKIE, SESSION_COOKIE, create_flask_app

HTTPS = "https://localhost"
ADMIN_PASSWORD = "correct horse battery staple"


def configured_services():
    engine = create_database_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    users = UserService(factory)
    sessions = SessionService(factory)
    users.bootstrap_organisation()
    return factory, users, sessions


@pytest.fixture
def interface():
    factory, users, sessions = configured_services()
    client = create_flask_app(users, sessions).test_client()
    return factory, users, sessions, client


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
    _, users, sessions = configured_services()
    client = TestClient(create_application(users, sessions), base_url="https://testserver")

    assert client.get("/api/health").json() == {"status": "ok"}
    assert "refrncr" in client.get("/").text
    assert client.get("/users").status_code == 403

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


def test_setup_requires_matching_csrf_and_can_only_complete_once(interface):
    factory, _, _, client = interface
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
    second_token = csrf_token(request(client, "get", "/setup"))
    response = request(
        client,
        "post",
        "/setup",
        data={**setup_data, "csrf_token": second_token},
    )
    assert response.status_code == 400
    assert "already been completed" in response_text(response)
    assert ADMIN_PASSWORD not in response_text(response)


def test_login_requires_csrf_uses_secure_cookie_and_hides_authentication_details(interface):
    _, _, _, client = interface
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

    for email, password in [
        ("admin@example.com", "wrong password"),
        ("unknown@example.com", "wrong password"),
        ("malformed email", "wrong password"),
    ]:
        response = login(client, email, password)
        assert response.status_code == 401
        assert response_text(response) == "Invalid credentials"
        assert email not in response_text(response)
        assert password not in response_text(response)


def test_successful_login_rotates_and_revokes_existing_session(interface):
    _, _, sessions, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    old_token = client.get_cookie(SESSION_COOKIE).value

    assert login(client).status_code == 302
    new_token = client.get_cookie(SESSION_COOKIE).value

    assert old_token != new_token
    assert sessions.resolve(old_token) is None
    assert sessions.resolve(new_token) is not None


def test_unauthenticated_and_non_admin_users_are_rejected(interface):
    _, users, _, client = interface
    setup_admin(client)
    assert request(client, "get", "/users").status_code == 403

    assert login(client).status_code == 302
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
    users.create_user(admin.id, "member@example.com", "Member", "member password")
    assert login(client, "member@example.com", "member password").status_code == 302
    assert request(client, "get", "/users").status_code == 403
    assert request(client, "post", "/users", data={}).status_code == 403


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/users", {"email": "new@example.com", "display_name": "New", "password": "password"}),
        ("/users/{target_id}/email", {"email": "changed@example.com"}),
        ("/users/{target_id}/disable", {}),
        ("/users/{target_id}/enable", {}),
        ("/users/{target_id}/delete", {}),
    ],
)
def test_every_user_management_post_rejects_missing_csrf(interface, path, data):
    _, users, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "managed@example.com", "Managed", "password")

    response = request(client, "post", path.format(target_id=managed.id), data=data)
    assert response.status_code == 403


def test_logout_requires_csrf_revokes_session_and_deletes_cookie(interface):
    _, _, sessions, client = interface
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
    _, users, sessions, client = interface
    setup_admin(client)
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "managed@example.com", "Managed", "member password")

    assert login(client, managed.email, "member password").status_code == 302
    disabled_token = client.get_cookie(SESSION_COOKIE).value
    users.disable_user(admin.id, managed.id)
    assert sessions.resolve(disabled_token) is None
    assert request(client, "get", "/users").status_code == 403
    assert login(client, managed.email, "member password").status_code == 401

    users.enable_user(admin.id, managed.id)
    assert login(client, managed.email, "member password").status_code == 302
    deleted_token = client.get_cookie(SESSION_COOKIE).value
    users.delete_user(admin.id, managed.id)
    assert sessions.resolve(deleted_token) is None
    assert request(client, "get", "/users").status_code == 403
    assert login(client, managed.email, "member password").status_code == 401


def test_user_list_escapes_user_controlled_values(interface):
    _, users, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
    users.create_user(
        admin.id,
        "<script>alert(1)</script>@example.com",
        "<img src=x onerror=alert(1)>",
        "password",
    )

    body = response_text(request(client, "get", "/users"))
    assert "<script>alert(1)</script>" not in body
    assert "<img src=x onerror=alert(1)>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;@example.com" in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body


def test_expected_user_error_does_not_expose_sensitive_values(interface):
    _, _, _, client = interface
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
    factory, _, _, client = interface
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
    _, users, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "managed@example.com", "Managed", "password")
    csrf = authenticated_csrf(client)

    response = request(
        client,
        "post",
        f"/users/{managed.id}/email",
        data={"email": "malformed email", "csrf_token": csrf},
    )

    assert response.status_code == 400
    assert "malformed email" not in response_text(response)
    assert users.authenticate("managed@example.com", "password").id == managed.id

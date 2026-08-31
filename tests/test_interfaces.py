import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.pool import StaticPool

from refrncr.app import create_application
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import Membership, Team, User
from refrncr.services import (
    AuthorizationError,
    MembershipService,
    SessionService,
    TeamService,
    UserService,
)
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
    teams = TeamService(factory)
    memberships = MembershipService(factory)
    users.bootstrap_organisation()
    return factory, users, sessions, teams, memberships


@pytest.fixture
def interface():
    factory, users, sessions, teams, memberships = configured_services()
    client = create_flask_app(users, sessions, teams, memberships).test_client()
    return factory, users, sessions, teams, memberships, client


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
    _, users, sessions, teams, memberships = configured_services()
    client = TestClient(
        create_application(users, sessions, teams, memberships), base_url="https://testserver"
    )

    assert client.get("/api/health").json() == {"status": "ok"}
    assert "refrncr" in client.get("/").text
    assert client.get("/users").status_code == 403
    assert client.get(f"/teams/{uuid.uuid4()}/members").status_code == 403

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


def test_setup_requires_matching_csrf_and_can_only_complete_once(interface):
    factory, _, _, _, _, client = interface
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
    _, _, _, _, _, client = interface
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
    _, _, sessions, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    old_token = client.get_cookie(SESSION_COOKIE).value

    assert login(client).status_code == 302
    new_token = client.get_cookie(SESSION_COOKIE).value

    assert old_token != new_token
    assert sessions.resolve(old_token) is None
    assert sessions.resolve(new_token) is not None


def test_unauthenticated_and_non_admin_users_are_rejected(interface):
    _, users, _, _, _, client = interface
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
    _, users, _, _, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "managed@example.com", "Managed", "password")

    response = request(client, "post", path.format(target_id=managed.id), data=data)
    assert response.status_code == 403


def test_logout_requires_csrf_revokes_session_and_deletes_cookie(interface):
    _, _, sessions, _, _, client = interface
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
    _, users, sessions, _, _, client = interface
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
    _, users, _, _, _, client = interface
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
    _, _, _, _, _, client = interface
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
    factory, _, _, _, _, client = interface
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
    _, users, _, _, _, client = interface
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


def test_admin_creates_renames_lists_and_deletes_team(interface):
    factory, _, _, _, _, client = interface
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


def test_team_interface_rejects_unauthenticated_and_regular_users(interface):
    _, users, _, teams, _, client = interface
    setup_admin(client)
    assert request(client, "get", "/teams").status_code == 403
    assert request(client, "post", "/teams", data={"name": "Forbidden"}).status_code == 403

    assert login(client).status_code == 302
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
    users.create_user(admin.id, "member@example.com", "Member", "member password")
    assert login(client, "member@example.com", "member password").status_code == 302
    assert request(client, "get", "/teams").status_code == 403
    assert request(client, "post", "/teams", data={"name": "Forbidden"}).status_code == 403
    assert teams.list_teams(admin.id) == []


@pytest.mark.parametrize("token", [None, "invalid", "é"])
@pytest.mark.parametrize("operation", ["create", "rename", "delete"])
def test_every_team_state_change_requires_csrf(interface, operation, token):
    _, users, _, teams, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
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
    _, users, _, teams, _, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
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
    factory, users, _, teams, memberships, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "member@example.com", "Member", "password")
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
    _, users, _, teams, memberships, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "member@example.com", "Member", "password")
    team = teams.create_team(admin.id, "Platform")
    membership = memberships.create_membership(admin.id, managed.id, team.id)
    if operation == "create":
        second = users.create_user(admin.id, "second@example.com", "Second", "password")
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
    _, users, _, teams, memberships, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "member@example.com", "Member", "password")
    team = teams.create_team(admin.id, "Platform")

    assert request(client, "get", f"/teams/{team.id}/members").status_code == 200
    request(client, "post", "/logout", data={"csrf_token": authenticated_csrf(client)})
    assert request(client, "get", f"/teams/{team.id}/members").status_code == 403

    actors = [
        users.create_user(admin.id, "regular@example.com", "Regular", "password"),
        users.create_user(admin.id, "disabled@example.com", "Disabled", "password", role="admin"),
        users.create_user(admin.id, "deleted@example.com", "Deleted", "password", role="admin"),
    ]
    assert login(client, "regular@example.com", "password").status_code == 302
    assert request(client, "get", f"/teams/{team.id}/members").status_code == 403
    assert login(client, "disabled@example.com", "password").status_code == 302
    users.disable_user(admin.id, actors[1].id)
    assert request(client, "get", f"/teams/{team.id}/members").status_code == 403
    assert login(client, "deleted@example.com", "password").status_code == 302
    users.delete_user(admin.id, actors[2].id)
    assert request(client, "get", f"/teams/{team.id}/members").status_code == 403
    for actor in actors:
        with pytest.raises(AuthorizationError):
            memberships.create_membership(actor.id, managed.id, team.id)
        with pytest.raises(AuthorizationError):
            memberships.delete_membership(actor.id, team.id, str(uuid.uuid4()))


def test_membership_interface_controls_invalid_targets_duplicates_and_lifecycle(interface):
    _, users, _, teams, memberships, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(admin.id, "member@example.com", "Member", "password")
    disabled = users.create_user(admin.id, "disabled@example.com", "Disabled", "password")
    deleted = users.create_user(admin.id, "deleted@example.com", "Deleted", "password")
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
    _, users, _, teams, memberships, client = interface
    setup_admin(client)
    assert login(client).status_code == 302
    admin = users.authenticate("admin@example.com", ADMIN_PASSWORD)
    managed = users.create_user(
        admin.id,
        "<script>alert(1)</script>@example.com",
        "<img src=x onerror=alert(1)>",
        "password",
    )
    team = teams.create_team(admin.id, "Platform")
    memberships.create_membership(admin.id, managed.id, team.id)

    body = response_text(request(client, "get", f"/teams/{team.id}/members"))
    assert "<script>alert(1)</script>" not in body
    assert "<img src=x onerror=alert(1)>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;@example.com" in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body

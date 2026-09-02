import re

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.pool import StaticPool

from refrncr.app import create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, Dataset, Grant
from refrncr.services import ResourceTypeRegistry
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


@pytest.mark.parametrize(
    ("method", "path", "data"),
    [
        ("get", "/datasets", None),
        ("get", "/datasets/new", None),
        ("post", "/datasets", {"key": "new", "name": "New", "description": ""}),
        ("get", "/datasets/example", None),
        ("get", "/datasets/example/edit", None),
        ("post", "/datasets/example", {"name": "Changed", "description": ""}),
    ],
)
def test_all_dataset_routes_require_authentication(dataset_browser, method, path, data):
    _, _, _, client = dataset_browser

    response = request(client, method, path, data=data)

    assert response.status_code == 403


def test_dataset_navigation_and_empty_list_state(dataset_browser):
    _, _, _, client = dataset_browser
    login(client)

    assert "Datasets" in body(request(client, "get", "/"))
    response = request(client, "get", "/datasets")

    assert response.status_code == 200
    assert "No datasets are available." in body(response)
    assert "Create dataset" in body(response)


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
    assert "/datasets/alpha-direct" in page
    assert "/datasets/beta-team" in page
    assert "hidden-owned" not in page
    assert "Create dataset" not in page
    assert request(client, "get", "/datasets/beta-team").status_code == 200


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


@pytest.mark.parametrize(
    ("overrides", "status"),
    [
        ({"key": "INVALID"}, 400),
        ({"name": "   "}, 400),
        ({"description": "x" * 10_001}, 400),
    ],
)
def test_create_validation_safely_redisplays_values(dataset_browser, overrides, status):
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
    assert submitted_markup not in body(response)
    if overrides.get("name") != "   ":
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body(response)
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
    assert markup not in body(response)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body(response)
    with factory() as session:
        persisted = session.get(Dataset, dataset.id)
        assert persisted.name == "Original"
        assert persisted.description == "Original"

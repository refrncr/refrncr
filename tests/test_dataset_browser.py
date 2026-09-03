import re

import pytest
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


def test_dataset_detail_field_empty_state_order_values_and_admin_controls(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "schema")
    login(client)

    empty_page = body(request(client, "get", "/datasets/schema"))
    assert "<h2>Fields</h2>" in empty_page
    assert "No fields are defined." in empty_page
    assert "Add field" in empty_page

    create_field(services, admin, dataset, "zeta", "Zeta", "date", True, False)
    create_field(services, admin, dataset, "alpha", "<script>alert(1)</script>", "string")
    page = body(request(client, "get", "/datasets/schema"))

    assert page.index("alpha") < page.index("zeta")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "string" in page and "date" in page
    assert "Required" in page and "Unique" in page
    assert page.count("Edit field") == 2


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
    assert "Record identifier: Not configured" in unconfigured
    assert "Business key" not in unconfigured

    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("zeta", "alpha"))
    configured = body(request(client, "get", "/datasets/business-key"))
    assert "Record identifier: <code>alpha</code> + <code>zeta</code>" in configured
    assert "🔑 Alpha" in configured
    assert "🔑 Zeta" in configured


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
    assert "Record identifier: <code>code</code>" in detail
    assert "🔑 Code" in detail
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
    assert "Part of Record identifier" in page
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
    assert "Part of Record identifier" in body(form)

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

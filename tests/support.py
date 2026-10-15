"""Reusable test helpers for browser-facing functional tests."""

import re

import pytest
from dataset_helpers import create_owned_dataset
from sqlalchemy.pool import StaticPool

from refrncr.app import create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.services import ResourceTypeRegistry
from refrncr.web import SESSION_COOKIE, create_flask_app

HTTPS = "https://localhost"
PASSWORD = "correct horse battery staple"


def _dataset_browser():
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
    services.teams.create_team(admin.id, "Test data domain")
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


@pytest.fixture
def dataset_browser():
    return _dataset_browser()


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
    return create_owned_dataset(
        services.datasets,
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

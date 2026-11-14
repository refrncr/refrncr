"""Public integration contract exercised without importing refrncr internals."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from refrncr.integration import (
    AuthorizationError,
    CliError,
    ConflictError,
    CreateAdminConfiguration,
    NotFoundError,
    ResourceTypeRegistry,
    ValidationError,
    create_application,
    create_community_services,
    create_database_engine,
    create_initial_admin,
    create_session_factory,
    database_is_ready,
    expected_database_revision,
    prepare_database,
    recover_administrator_password,
)
from refrncr.integration import (
    __all__ as public_names,
)


def test_integration_exposes_application_and_error_contract():
    assert set(public_names) == {
        "AuthorizationError",
        "CliError",
        "ConflictError",
        "CreateAdminConfiguration",
        "NotFoundError",
        "ResourceTypeRegistry",
        "ValidationError",
        "create_application",
        "create_community_services",
        "create_database_engine",
        "create_initial_admin",
        "create_session_factory",
        "database_is_ready",
        "expected_database_revision",
        "prepare_database",
        "recover_administrator_password",
    }
    assert callable(create_application)
    assert callable(create_community_services)
    assert callable(create_database_engine)
    assert callable(create_session_factory)
    assert callable(prepare_database)
    assert callable(database_is_ready)
    assert callable(expected_database_revision)
    assert callable(create_initial_admin)
    assert callable(recover_administrator_password)
    assert issubclass(NotFoundError, Exception)
    assert issubclass(ConflictError, Exception)
    assert issubclass(ValidationError, Exception)
    assert issubclass(AuthorizationError, Exception)
    assert issubclass(CliError, Exception)


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)])
def integration_database(request, tmp_path):
    administration = None
    schema = None
    if request.param == "postgresql":
        database_url = os.environ["REFRNCR_TEST_POSTGRES_URL"]
        administration = create_engine(database_url)
        schema = f"integration_{uuid.uuid4().hex}"
        with administration.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    else:
        url = make_url(f"sqlite:///{tmp_path / 'integration.db'}")

    prepare_database(url)
    engine = create_database_engine(url)
    try:
        yield engine, url.render_as_string(hide_password=False)
    finally:
        engine.dispose()
        if administration is not None:
            with administration.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            administration.dispose()


def test_integration_contract_on_supported_databases(integration_database):
    engine, database_url = integration_database
    sessions = create_session_factory(engine)
    services = create_community_services(sessions, ResourceTypeRegistry())
    application = create_application(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        authorization=services.authorization,
        datasets=services.datasets,
        dataset_fields=services.dataset_fields,
        dataset_business_keys=services.dataset_business_keys,
        dataset_records=services.dataset_records,
        record_imports=services.record_imports,
        hierarchy_imports=services.hierarchy_imports,
        audit_history=services.audit_history,
        dataset_versions=services.dataset_versions,
        api_keys=services.api_keys,
        readiness_engine=engine,
    )

    with TestClient(application, base_url="https://testserver") as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert sessions.kw["expire_on_commit"] is False
    assert database_is_ready(engine)
    assert expected_database_revision()

    email = "administrator@example.com"
    old_password = "correct horse battery staple"
    new_password = "another correct horse battery staple"
    create_passwords = iter((old_password,) * 2)
    create_initial_admin(
        CreateAdminConfiguration(database_url, email, "Administrator"),
        password_reader=lambda _prompt: next(create_passwords),
    )

    with pytest.raises(NotFoundError) as missing:
        recover_administrator_password(sessions, "missing@example.com", new_password)
    assert not isinstance(missing.value, ConflictError | ValidationError | AuthorizationError)

    with pytest.raises(ValidationError) as invalid:
        recover_administrator_password(sessions, email, "too short")
    assert not isinstance(invalid.value, NotFoundError | ConflictError | AuthorizationError)

    recover_administrator_password(sessions, email, new_password)

    assert services.authentication.authenticate(email, old_password) is None
    assert services.authentication.authenticate(email, new_password) is not None

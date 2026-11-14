"""Public integration contract exercised without importing refrncr internals."""

from pathlib import Path

from refrncr.integration import (
    AuthorizationError,
    CliError,
    ConflictError,
    CreateAdminConfiguration,
    NotFoundError,
    RecoverAdminConfiguration,
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
    recover_admin_password,
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
        "RecoverAdminConfiguration",
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
        "recover_admin_password",
    }
    assert callable(create_application)
    assert callable(create_community_services)
    assert callable(create_database_engine)
    assert callable(create_session_factory)
    assert callable(prepare_database)
    assert callable(database_is_ready)
    assert callable(expected_database_revision)
    assert callable(create_initial_admin)
    assert callable(recover_admin_password)
    assert issubclass(NotFoundError, Exception)
    assert issubclass(ConflictError, Exception)
    assert issubclass(ValidationError, Exception)
    assert issubclass(AuthorizationError, Exception)
    assert issubclass(CliError, Exception)


def test_integration_composes_services_on_supported_databases(database_audit_db):
    engine, sessions, _existing_services = database_audit_db
    services = create_community_services(sessions, ResourceTypeRegistry())

    assert services.users is not None
    assert services.datasets is not None
    assert create_session_factory(engine).kw["expire_on_commit"] is False


def test_integration_prepares_and_reports_sqlite_readiness(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'integration.db'}"

    prepare_database(database_url)
    engine = create_database_engine(database_url)
    try:
        assert database_is_ready(engine)
        assert expected_database_revision()
    finally:
        engine.dispose()


def test_integration_admin_creation_and_recovery(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'administrators.db'}"
    create_passwords = iter(("correct horse battery staple",) * 2)
    recover_passwords = iter(("another correct horse battery staple",) * 2)

    create_initial_admin(
        CreateAdminConfiguration(database_url, "admin@example.com", "Administrator"),
        password_reader=lambda _prompt: next(create_passwords),
    )
    recover_admin_password(
        RecoverAdminConfiguration(database_url, "admin@example.com"),
        password_reader=lambda _prompt: next(recover_passwords),
    )

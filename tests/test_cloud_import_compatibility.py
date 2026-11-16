"""Temporary contract for the import paths still used by refrncr Cloud."""

from pathlib import Path

import pytest


def test_cloud_production_import_paths_remain_available():
    from refrncr.app import create_application, create_community_services
    from refrncr.cli import (
        CliError,
        CreateAdminConfiguration,
        build_application,
        create_initial_admin,
        prepare_database,
    )

    with pytest.warns(DeprecationWarning, match=r"refrncr\.integration"):
        from refrncr.db.session import create_database_engine, create_session_factory

    from refrncr.errors import ValidationError
    from refrncr.operational import database_is_ready, expected_database_revision
    from refrncr.persistence import SqlAlchemyUnitOfWorkFactory, SqlAlchemyUserOperations
    from refrncr.services import (
        AuthenticationService,
        ConflictError,
        NotFoundError,
        ResourceTypeRegistry,
        SessionService,
        UserService,
    )

    assert all(
        callable(value)
        for value in (
            create_application,
            create_community_services,
            build_application,
            prepare_database,
            create_initial_admin,
            database_is_ready,
            expected_database_revision,
            create_database_engine,
            create_session_factory,
            CliError,
            CreateAdminConfiguration,
            ValidationError,
            SqlAlchemyUnitOfWorkFactory,
            SqlAlchemyUserOperations,
            UserService,
            ResourceTypeRegistry,
            ConflictError,
            NotFoundError,
            AuthenticationService,
            SessionService,
        )
    )


def test_cloud_test_import_paths_and_migrations_remain_available():
    import refrncr

    with pytest.warns(DeprecationWarning, match=r"refrncr\.integration"):
        from refrncr.models import AuditEvent, LoginSession, User

    assert all(isinstance(value, type) for value in (AuditEvent, LoginSession, User))
    assert (Path(refrncr.__file__).parent / "migrations").is_dir()

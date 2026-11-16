"""Supported integration surface for applications embedding refrncr Community."""

from refrncr.app import ServiceWrappers, create_application, create_community_services
from refrncr.authorization import ResourceTypeRegistry
from refrncr.cli import (
    CliError,
    CreateAdminConfiguration,
    create_initial_admin,
    prepare_database,
)
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from refrncr.operational import database_is_ready, expected_database_revision

from .administrators import recover_administrator_password

__all__ = (
    "AuthorizationError",
    "CliError",
    "ConflictError",
    "CreateAdminConfiguration",
    "NotFoundError",
    "ResourceTypeRegistry",
    "ServiceWrappers",
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
)

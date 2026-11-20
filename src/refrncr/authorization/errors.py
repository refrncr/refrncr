"""Access-management domain errors."""

from refrncr.errors import (
    AuthorizationError,
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)


class AccessManagementError(DomainError):
    """Access-management-domain failure."""


class AccessManagementNotFoundError(AccessManagementError, NotFoundError):
    """An access-management object does not exist."""


class AccessManagementConflictError(AccessManagementError, ConflictError):
    """Access-management state conflicts with the requested work."""


class AccessManagementValidationError(AccessManagementError, ValidationError):
    """Access-management input violates a domain constraint."""


class AccessManagementAuthorizationError(AccessManagementError, AuthorizationError):
    """An actor lacks authority for access-management work."""

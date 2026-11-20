"""Shared, persistence-neutral domain error hierarchy."""


class DomainError(Exception):
    """Base class for safe domain errors exposed at an application boundary."""


# Temporary compatibility name. REF-353 removes it after integrators have moved to
# the category-based public contract in ``refrncr.integration``.
UserManagementError = DomainError


class NotFoundError(DomainError):
    """A requested domain object does not exist or is deliberately concealed."""


class ConflictError(DomainError):
    """Requested work conflicts with current domain state."""

    def __init__(self, message, *, field=None):
        self.field = field
        super().__init__(message)


class ValidationError(DomainError, ValueError):
    """A request contains a value that violates a domain constraint."""

    def __init__(self, message, *, field=None):
        self.field = field
        super().__init__(message)


class AuthorizationError(DomainError):
    """An authenticated actor is not authorized to perform the requested operation."""

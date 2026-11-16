from refrncr.errors import (
    AuthorizationError,
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)


class VersionError(DomainError):
    """Dataset-version domain failure."""


class VersionNotFoundError(VersionError, NotFoundError):
    """A version resource does not exist or is deliberately concealed."""


class VersionConflictError(VersionError, ConflictError):
    """Version work conflicts with current domain state."""


class VersionValidationError(VersionError, ValidationError):
    """A version request violates a domain constraint."""


class VersionAuthorizationError(VersionError, AuthorizationError):
    """An authenticated actor cannot perform requested version work."""

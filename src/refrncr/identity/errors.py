"""Identity-domain errors categorized for application boundaries."""

from refrncr.errors import (
    AuthorizationError,
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)


class IdentityError(DomainError):
    """Identity-domain failure."""


class IdentityNotFoundError(IdentityError, NotFoundError):
    """An identity-domain object does not exist or is deliberately concealed."""


class IdentityConflictError(IdentityError, ConflictError):
    """Identity work conflicts with current state."""


class IdentityValidationError(IdentityError, ValidationError):
    """Identity input violates a domain constraint."""


class IdentityAuthorizationError(IdentityError, AuthorizationError):
    """An authenticated actor is denied an identity operation."""


class InvalidLifecycleError(IdentityValidationError):
    """An identity lifecycle transition is invalid for the requested operation."""

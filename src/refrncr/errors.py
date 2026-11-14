"""Shared, persistence-neutral domain error hierarchy."""


class DomainError(Exception):
    """Base class for safe domain errors exposed at an application boundary."""


# Temporary compatibility name. REF-353 removes it after integrators have moved to
# the category-based public contract in ``refrncr.integration``.
UserManagementError = DomainError


class IdentityError(DomainError):
    """Identity-domain failure."""


class AccessManagementError(DomainError):
    """Access-management-domain failure."""


class DatasetError(DomainError):
    """Dataset-domain failure."""


class VersionError(DomainError):
    """Dataset-version-domain failure."""


class ImportDomainError(DomainError):
    """Import-domain failure; named distinctly from Python's built-in ImportError."""


class HierarchyError(DomainError):
    """Hierarchy-domain failure."""


class PersistenceError(DomainError):
    """Persistence-boundary failure safe to expose as a domain error."""


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


class InvalidLifecycleError(IdentityError, ValidationError):
    """An identity lifecycle transition is invalid for the requested operation."""


class RecordValidationError(DatasetError, ValidationError):
    """Record values violate one or more Dataset field constraints."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("Record contains invalid values")
        self.errors = errors


class RecordConflictError(DatasetError, ConflictError):
    """Record work conflicts with current Dataset state."""

    def __init__(self, message: str, field_key: str | None = None) -> None:
        super().__init__(message)
        self.field_key = field_key


class StaleRecordError(DatasetError, ConflictError):
    """The submitted Record revision no longer matches persisted state."""


class StaleFieldOrderError(DatasetError, ConflictError):
    """The submitted field order no longer matches persisted state."""


class StaleDatasetOwnerError(DatasetError, ConflictError):
    """Persisted ownership no longer matches the form's expected owner."""

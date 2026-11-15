"""Shared, persistence-neutral domain error hierarchy."""


class DomainError(Exception):
    """Base class for safe domain errors exposed at an application boundary."""


# Temporary compatibility name. REF-353 removes it after integrators have moved to
# the category-based public contract in ``refrncr.integration``.
UserManagementError = DomainError


class AccessManagementError(DomainError):
    """Access-management-domain failure."""


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


_DATASET_ERROR_NAMES = frozenset(
    {
        "DatasetError",
        "DatasetNotFoundError",
        "DatasetConflictError",
        "DatasetValidationError",
        "RecordValidationError",
        "RecordConflictError",
        "StaleRecordError",
        "StaleFieldOrderError",
        "StaleDatasetOwnerError",
    }
)


def __getattr__(name: str):
    """Temporarily preserve dataset-error imports until REF-370."""
    if name in _DATASET_ERROR_NAMES:
        from refrncr.datasets import errors as dataset_errors

        return getattr(dataset_errors, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Shared, persistence-neutral domain error hierarchy."""

from importlib import import_module


class DomainError(Exception):
    """Base class for safe domain errors exposed at an application boundary."""


# Temporary compatibility name. REF-353 removes it after integrators have moved to
# the category-based public contract in ``refrncr.integration``.
UserManagementError = DomainError


class AccessManagementError(DomainError):
    """Access-management-domain failure."""


class VersionError(DomainError):
    """Dataset-version-domain failure."""


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


_MOVED_ERROR_MODULES = {
    "HierarchyError": "refrncr.hierarchies.errors",
    "ImportDomainError": "refrncr.imports.errors",
}

_DATASET_ERROR_NAMES = frozenset(
    {
        "DatasetError",
        "DatasetAuthorizationError",
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
    """Temporarily preserve moved domain-error imports until REF-370."""
    if module := _MOVED_ERROR_MODULES.get(name):
        value = getattr(import_module(module), name)
        globals()[name] = value
        return value
    if name in _DATASET_ERROR_NAMES:
        value = getattr(import_module("refrncr.datasets.errors"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

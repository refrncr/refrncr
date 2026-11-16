"""Dataset version domain services and contracts."""

from importlib import import_module

from refrncr.versions.errors import (
    VersionAuthorizationError,
    VersionConflictError,
    VersionError,
    VersionNotFoundError,
    VersionValidationError,
)

__all__ = [
    "DatasetVersionService",
    "VersionAuthorizationError",
    "VersionConflictError",
    "VersionError",
    "VersionNotFoundError",
    "VersionValidationError",
    "validated_snapshot",
]


def __getattr__(name: str):
    if name in {"DatasetVersionService", "validated_snapshot"}:
        service = import_module("refrncr.versions.service")
        return getattr(service, name)
    raise AttributeError(name)

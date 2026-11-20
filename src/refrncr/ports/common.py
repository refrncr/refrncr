"""Persistence-neutral port contracts."""

from __future__ import annotations

from typing import Any, Protocol


class AuthorizationServicePort(Protocol):
    """Dataset-facing authorization contract without a domain-package dependency."""

    datasets: Any

    def has_capability(
        self, user_id: str, capability: str, resource_id=None, *, uow=None
    ) -> bool: ...


class CommunityServicesPort(Protocol):
    """Browser-facing view of the service bundle supplied by composition."""

    clock: Any
    installation_settings: Any
    api_keys: Any
    dataset_versions: Any
    audit_history: Any
    users: Any
    authentication: Any
    sessions: Any
    teams: Any
    memberships: Any
    authorization: Any
    datasets: Any
    dataset_fields: Any
    dataset_business_keys: Any
    dataset_records: Any
    record_imports: Any
    hierarchy_imports: Any
    hierarchy: Any
    overview: Any
    dataset_diff: Any


class PersistenceConflictError(Exception):
    """A persistence constraint rejected the pending unit-of-work changes."""

    def __init__(self, constraint: str | None = None) -> None:
        super().__init__("A persistence constraint rejected the operation")
        self.constraint = constraint


class PasswordHashReplacementError(Exception):
    """A password-hash replacement could not be persisted safely."""

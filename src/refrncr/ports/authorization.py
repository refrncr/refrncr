"""Persistence-neutral port contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AuthorizationSubject:
    exists: bool
    active: bool
    deleted: bool
    community_admin: bool


@dataclass(frozen=True)
class PermissionContext:
    subject: AuthorizationSubject
    permissions: tuple[str, ...]


@dataclass(frozen=True)
class ResourcePermissionsContext:
    subject: AuthorizationSubject
    permissions: tuple[tuple[str, str], ...]


class PermissionDataSource(Protocol):
    def load_context(
        self, user_id: str, resource_type: str, resource_id: str
    ) -> PermissionContext: ...

    def load_resource_permissions(
        self, user_id: str, resource_type: str
    ) -> ResourcePermissionsContext: ...

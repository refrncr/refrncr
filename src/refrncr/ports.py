from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class PersistenceConflictError(Exception):
    """A persistence constraint rejected the pending unit-of-work changes."""

    def __init__(self, constraint: str | None = None) -> None:
        super().__init__("A persistence constraint rejected the operation")
        self.constraint = constraint


class PasswordHashReplacementError(Exception):
    """A password-hash replacement could not be persisted safely."""


@dataclass(frozen=True)
class AuditEntry:
    actor_user_id: str
    object_type: str
    object_id: str
    action: str


class OrganisationRecord(Protocol):
    id: str


class UserRecord(Protocol):
    id: str
    email: str
    display_name: str
    password_hash: str
    role: str
    is_active: bool
    is_deleted: bool
    updated_at: datetime


class TeamRecord(Protocol):
    id: str
    name: str
    name_key: str
    updated_at: datetime


class MembershipRecord(Protocol):
    id: str
    user_id: str
    team_id: str


class GrantRecord(Protocol):
    id: str
    subject_type: str
    subject_id: str
    resource_type: str
    resource_id: str
    permission: str
    updated_at: datetime


class DatasetRecord(Protocol):
    id: str
    key: str
    name: str
    description: str
    owner_user_id: str
    created_at: datetime
    updated_at: datetime


class DatasetFieldRecord(Protocol):
    id: str
    dataset_id: str
    key: str
    name: str
    data_type: str
    is_required: bool
    is_unique: bool
    created_at: datetime
    updated_at: datetime


class UserOperations(Protocol):
    def get(self, user_id: str, *, for_update: bool = False) -> UserRecord | None: ...

    def find_by_email(self, email: str) -> UserRecord | None: ...

    def list_by_email(self) -> list[UserRecord]: ...

    def count(self) -> int: ...

    def count_active_admins(self) -> int: ...

    def create(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
        role: str,
        is_active: bool,
        is_deleted: bool,
    ) -> UserRecord: ...

    def replace_password_hash(
        self, user_id: str, expected_hash: str, replacement_hash: str
    ) -> bool: ...

    def revoke_active_sessions(self, user_id: str, revoked_at: datetime) -> None: ...

    def get_community_organisation(self) -> OrganisationRecord | None: ...

    def create_community_organisation(self) -> OrganisationRecord: ...

    def lock_community_setup(self) -> OrganisationRecord | None: ...


class TeamOperations(Protocol):
    def get(self, team_id: str, *, for_update: bool = False) -> TeamRecord | None: ...

    def list_by_name(self) -> list[TeamRecord]: ...

    def create(self, *, name: str) -> TeamRecord: ...

    def delete(self, team: TeamRecord) -> None: ...


class MembershipOperations(Protocol):
    def get(self, membership_id: str, *, for_update: bool = False) -> MembershipRecord | None: ...

    def create(self, *, user_id: str, team_id: str) -> MembershipRecord: ...

    def delete(self, membership: MembershipRecord) -> None: ...

    def list_team_members(self, team_id: str) -> list[tuple[MembershipRecord, UserRecord]]: ...

    def list_eligible_users(self, team_id: str) -> list[UserRecord]: ...


class GrantOperations(Protocol):
    def get(self, grant_id: str, *, for_update: bool = False) -> GrantRecord | None: ...

    def create(
        self,
        *,
        subject_type: str,
        subject_id: str,
        resource_type: str,
        resource_id: str,
        permission: str,
    ) -> GrantRecord: ...

    def delete(self, grant: GrantRecord) -> None: ...

    def list_for_team(self, team_id: str, *, for_update: bool = False) -> list[GrantRecord]: ...


class DatasetOperations(Protocol):
    def get(self, dataset_id: str, *, for_update: bool = False) -> DatasetRecord | None: ...

    def find_by_key(self, key: str, *, for_update: bool = False) -> DatasetRecord | None: ...

    def list_all(self) -> list[DatasetRecord]: ...

    def list_by_ids(self, dataset_ids: tuple[str, ...]) -> list[DatasetRecord]: ...

    def exists(self, dataset_id: str) -> bool: ...

    def create(
        self,
        *,
        key: str,
        name: str,
        description: str,
        owner_user_id: str,
    ) -> DatasetRecord: ...


class DatasetFieldOperations(Protocol):
    def find_by_key(
        self, dataset_id: str, key: str, *, for_update: bool = False
    ) -> DatasetFieldRecord | None: ...

    def list_for_dataset(self, dataset_id: str) -> list[DatasetFieldRecord]: ...

    def create(
        self,
        *,
        dataset_id: str,
        key: str,
        name: str,
        data_type: str,
        is_required: bool,
        is_unique: bool,
    ) -> DatasetFieldRecord: ...


class UnitOfWork(Protocol):
    users: UserOperations
    teams: TeamOperations
    memberships: MembershipOperations
    grants: GrantOperations
    datasets: DatasetOperations
    dataset_fields: DatasetFieldOperations

    def flush(self) -> None: ...

    def add_audit(
        self,
        *,
        actor_user_id: str,
        object_type: str,
        object_id: str,
        action: str,
    ) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(
        self, *, write: bool = False, serialized: bool = False
    ) -> AbstractContextManager[UnitOfWork]: ...


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

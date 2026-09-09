from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
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
    owner_team_id: str
    owner_team_name: str
    created_at: datetime
    updated_at: datetime
    content_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class DatasetOverview:
    dataset: DatasetRecord
    record_count: int
    last_updated: datetime
    owner_team_name: str = ""

    def __post_init__(self) -> None:
        value = self.last_updated
        # SQLite returns naive values for timestamps stored in UTC.
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        object.__setattr__(self, "last_updated", value)


class DatasetFieldRecord(Protocol):
    id: str
    dataset_id: str
    key: str
    name: str
    data_type: str
    is_required: bool
    is_unique: bool
    display_position: int
    created_at: datetime
    updated_at: datetime


class DatasetRecordRow(Protocol):
    id: str
    dataset_id: str
    identifier_key: bytes
    revision: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecordValue:
    field_id: str
    field_key: str
    data_type: str
    value: str | Decimal | bool | date
    canonical_key: bytes
    is_unique: bool


@dataclass(frozen=True, slots=True)
class StoredRecord:
    id: str
    dataset_id: str
    values: tuple[RecordValue, ...]
    revision: int


@dataclass(frozen=True, slots=True)
class DatasetBusinessKey:
    dataset_id: str
    field_keys: tuple[str, ...]


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
    def team_ids_for_user(self, user_id: str) -> tuple[str, ...]: ...

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
    def owns_datasets(self, team_id: str) -> bool: ...

    def change_owner_if_expected(
        self, dataset_id: str, expected_owner_team_id: str, owner_team_id: str
    ) -> bool: ...

    def current_overview(self, dataset_ids: tuple[str, ...] | None) -> list[DatasetOverview]:
        """Current working set only; None is reserved for authorized all-resource access."""
        ...

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
        owner_team_id: str,
    ) -> DatasetRecord: ...


class DatasetFieldOperations(Protocol):
    def find_by_key(
        self, dataset_id: str, key: str, *, for_update: bool = False
    ) -> DatasetFieldRecord | None: ...

    def list_for_dataset(self, dataset_id: str) -> list[DatasetFieldRecord]: ...

    def replace_order(self, dataset_id: str, field_ids: tuple[str, ...]) -> None: ...

    def create(
        self,
        *,
        dataset_id: str,
        key: str,
        name: str,
        data_type: str,
        is_required: bool,
        is_unique: bool,
        display_position: int,
    ) -> DatasetFieldRecord: ...

    def delete(self, field: DatasetFieldRecord) -> None: ...


class DatasetBusinessKeyOperations(Protocol):
    def get(self, dataset_id: str) -> DatasetBusinessKey: ...

    def contains(self, dataset_id: str, field_key: str) -> bool: ...

    def replace(self, dataset_id: str, field_keys: tuple[str, ...]) -> None: ...


class DatasetRecordOperations(Protocol):
    def create(self, *, dataset_id: str, identifier_key: bytes) -> DatasetRecordRow: ...

    def get(self, dataset_id: str, record_id: str) -> StoredRecord | None: ...

    def update_if_revision(
        self,
        dataset_id: str,
        record_id: str,
        expected_revision: int,
        identifier_key: bytes,
    ) -> bool: ...

    def replace_values(self, record: DatasetRecordRow, values: tuple[RecordValue, ...]) -> None: ...

    def delete_if_revision(
        self, dataset_id: str, record_id: str, expected_revision: int
    ) -> bool: ...

    def current_revision(self, dataset_id: str, record_id: str) -> int | None: ...

    def add_values(self, record: DatasetRecordRow, values: tuple[RecordValue, ...]) -> None: ...

    def list_all(self, dataset_id: str) -> list[StoredRecord]: ...

    def iterate_all(self, dataset_id: str) -> Iterator[StoredRecord]: ...

    def existing_identifier_keys(
        self, dataset_id: str, identifier_keys: tuple[bytes, ...]
    ) -> set[bytes]: ...

    def existing_unique_value_keys(
        self, dataset_id: str, values: tuple[tuple[str, bytes], ...]
    ) -> set[tuple[str, bytes]]: ...

    def count(self, dataset_id: str) -> int: ...

    def field_has_values(self, dataset_id: str, field_id: str) -> bool: ...

    def field_has_missing_values(self, dataset_id: str, field_id: str) -> bool: ...

    def field_has_duplicate_values(self, dataset_id: str, field_id: str) -> bool: ...

    def set_field_unique_keys(self, dataset_id: str, field_id: str, enabled: bool) -> None: ...


class UnitOfWork(Protocol):
    users: UserOperations
    teams: TeamOperations
    memberships: MembershipOperations
    grants: GrantOperations
    datasets: DatasetOperations
    dataset_fields: DatasetFieldOperations
    dataset_business_keys: DatasetBusinessKeyOperations
    dataset_records: DatasetRecordOperations
    dataset_export_access: DatasetExportAccessOperations

    def begin_export_snapshot(self) -> None: ...

    def flush(self) -> None: ...

    def add_audit(
        self,
        *,
        actor_user_id: str,
        object_type: str,
        object_id: str,
        action: str,
        details: dict[str, str] | None = None,
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


class DatasetExportAccessRecord(Protocol):
    id: str
    dataset_id: str
    subject_type: str
    subject_id: str
    created_at: datetime


class DatasetExportAccessOperations(Protocol):
    def get(self, access_id: str) -> DatasetExportAccessRecord | None: ...

    def list_for_dataset(self, dataset_id: str) -> list[DatasetExportAccessRecord]: ...

    def applies(self, dataset_id: str, user_id: str, team_ids: tuple[str, ...]) -> bool: ...

    def create(
        self, dataset_id: str, subject_type: str, subject_id: str
    ) -> DatasetExportAccessRecord: ...

    def delete(self, access: DatasetExportAccessRecord) -> None: ...

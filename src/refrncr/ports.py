from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
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


@dataclass(frozen=True, slots=True)
class AuditFilters:
    start: datetime | None = None
    end: datetime | None = None
    actor: str = ""
    action: str = ""
    object_type: str = ""


@dataclass(frozen=True, slots=True)
class AuditPosition:
    timestamp: datetime
    id: str


class AuditEventRecord(Protocol):
    id: str
    timestamp: datetime
    actor_user_id: str | None
    object_type: str
    object_id: str
    action: str
    details: object


class AuditReadUnavailable(Exception):
    """Audit persistence could not complete a read; contains no database details."""


AUDIT_NAMED_OBJECT_TYPES = frozenset({"user", "team", "dataset", "dataset_field"})


@dataclass(frozen=True, slots=True)
class AuditObjectRecord:
    """Authoritative existence result, independent of an absent/unsafe display name."""

    exists: bool
    name: str | None = None


class AuditReadOperations(Protocol):
    def page(
        self,
        filters: AuditFilters,
        *,
        before: AuditPosition | None,
        ceiling: AuditPosition | None,
        newer: bool = False,
    ) -> tuple[AuditEventRecord, ...]:
        """At most 51 events: descending timestamp/ID, ascending when newer=True."""
        ...

    def get(self, event_id: str) -> AuditEventRecord | None: ...

    def actor_names(self, ids: tuple[str, ...]) -> dict[str, str]: ...

    def resolve_objects(
        self, object_type: str, ids: tuple[str, ...]
    ) -> dict[str, AuditObjectRecord]:
        """One explicit result per ID for a supported named type; failures must raise."""
        ...


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


@dataclass(frozen=True, slots=True)
class MembershipRelationship:
    user_id: str
    user_name: str
    team_id: str
    team_name: str


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
    icon: str | None
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
    revision: int


@dataclass(frozen=True, slots=True)
class DatasetConfiguration:
    dataset: DatasetRecord
    may_maintain: bool
    may_govern: bool


@dataclass(frozen=True, slots=True)
class DatasetOverview:
    dataset: DatasetRecord
    record_count: int
    last_updated: datetime
    owner_team_name: str = ""
    concept_in_progress: bool = False
    no_current: bool = False

    def __post_init__(self) -> None:
        value = self.last_updated
        # SQLite returns naive values for timestamps stored in UTC.
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        object.__setattr__(self, "last_updated", value)


class DatasetFieldRecord(Protocol):
    definition: str | None
    unit: str | None
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
    value: str | int | Decimal | float | bool | date | time | datetime
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
    def list_relationships(self) -> tuple[MembershipRelationship, ...]: ...

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
    def deletion_counts(self, dataset_id: str) -> tuple[int, int, int, int]:
        """Fields, records, versions and export access grants the deletion removes."""
        ...

    def delete_with_dependents(self, dataset_id: str) -> None:
        """Delete the inventoried operational rows; never delete audit history or commit."""
        ...

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
        icon: str | None = None,
    ) -> DatasetRecord: ...


class DatasetFieldOperations(Protocol):
    def source_formats(self, dataset_id: str) -> dict[str, dict[str, str]]: ...

    def remember_source_formats(
        self, dataset_id: str, choices: dict[str, dict[str, str]]
    ) -> None: ...

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
        definition: str | None = None,
        unit: str | None = None,
    ) -> DatasetFieldRecord: ...

    def delete(self, field: DatasetFieldRecord) -> None: ...


class DatasetBusinessKeyOperations(Protocol):
    def get(self, dataset_id: str) -> DatasetBusinessKey: ...

    def contains(self, dataset_id: str, field_key: str) -> bool: ...

    def replace(self, dataset_id: str, field_keys: tuple[str, ...]) -> None: ...


class DatasetRecordOperations(Protocol):
    def deleted_records(self, dataset_id): ...

    def restore_if_revision(self, dataset_id, record_id, expected_revision, identifier_key): ...

    def identifier_keys(self, dataset_id: str) -> dict[str, bytes]: ...

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


class ApiKeyRecord(Protocol):
    id: str
    owner_id: str
    name: str
    verifier: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None
    revision: int


class ApiKeyOperations(Protocol):
    def get(self, key_id: str, *, fresh: bool = False) -> ApiKeyRecord | None: ...
    def get_owned(self, owner_id: str, key_id: str) -> ApiKeyRecord | None: ...
    def list_owned(self, owner_id: str) -> tuple[ApiKeyRecord, ...]: ...
    def active_owned(self, owner_id: str, now: datetime) -> tuple[ApiKeyRecord, ...]: ...
    def create(self, **values) -> ApiKeyRecord: ...


class UnitOfWork(Protocol):
    write: bool
    api_keys: ApiKeyOperations
    concepts: ConceptOperations
    version_diff: VersionDiffOperations
    versions: VersionOperations
    audit_history: AuditReadOperations
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
        actor_user_id: str | None,
        object_type: str,
        object_id: str,
        action: str,
        details: dict[str, str | int] | None = None,
    ) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(
        self, *, write: bool = False, serialized: bool = False, translate_errors: bool = False
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


class VersionOperations(Protocol):
    """Append complete snapshots; never expose mutable rows or individual deletion.

    Append and cancellation require the parent Dataset write lock. All operations
    participate in the caller's UoW; none commits. Content/results are immutable.
    """

    def list_for_dataset(self, dataset_id): ...

    def get(self, dataset_id, version_id): ...

    def latest_non_cancelled(self, dataset_id): ...

    def current_for_datasets(self, dataset_ids, today): ...

    def current_page(self, today, *, dataset_ids=None, offset, limit): ...

    def record_identity(self, version, identifier_key): ...

    def record_page(self, version, fields, *, offset, limit): ...

    def append(
        self,
        dataset_id,
        number,
        published_at,
        publisher_id,
        effective_date,
        release_note,
        content,
        summary,
    ): ...

    def cancel(self, dataset_id, version_id, actor_id, timestamp, today): ...

    def fields(self, version): ...

    def iterate_records(self, version): ...

    def content(self, version): ...


class VersionDiffOperations(Protocol):
    """Bounded scalar diff queries in the caller's coherent transaction."""

    def counts(self, dataset_id, version_id): ...

    def record_states(self, dataset_id, version_id, record_ids): ...

    def removed_records(self, dataset_id, version): ...

    def records(self, dataset_id, version_id, statuses, record_id=None): ...

    def validate_snapshot(self, version): ...

    def validate_concept(self, dataset_id, fields): ...

    def values(self, dataset_id, version_id, record_id, status, page): ...


class ConceptOperations(Protocol):
    def record_mutation(self, dataset_id, *, import_result=None): ...

    def get(self, dataset_id): ...
    def list_for_datasets(self, dataset_ids): ...
    def create(self, dataset_id, base_version_id, creator_id, now, revision): ...
    def touch(self, dataset_id, now): ...
    def close(self, dataset_id): ...
    def discard(self, dataset, current): ...
    def initialize(self, dataset, version, now): ...

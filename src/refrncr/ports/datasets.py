"""Persistence-neutral port contracts."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Protocol


class DatasetRecord(Protocol):
    icon: str | None
    id: str
    key: str
    name: str
    description: str
    kind: str
    mapping_mode: str | None
    source_system: str | None
    target_system: str | None
    source_version_id: str | None
    source_dataset_id: str | None
    target_dataset_id: str | None
    source_field_key: str | None
    target_field_key: str | None
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
    may_edit_key: bool


@dataclass(frozen=True, slots=True)
class DatasetOverview:
    dataset: DatasetRecord
    record_count: int
    last_updated: datetime
    owner_team_name: str = ""
    concept_in_progress: bool = False
    no_current: bool = False
    source_update_available: bool = False


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
    mapping_role: str | None
    source_field_id: str | None
    source_field_key: str | None
    hierarchy_scope: str | None
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
class RecordPage:
    records: tuple[StoredRecord, ...]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True, slots=True)
class DatasetBusinessKey:
    dataset_id: str
    field_keys: tuple[str, ...]


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

    def rename_key(self, dataset_id: str, key: str) -> None: ...

    def uses_linked_field(self, dataset_id: str, field_key: str) -> bool: ...

    def current_overview(self, dataset_ids: tuple[str, ...] | None) -> list[DatasetOverview]:
        """Current working set only; None is reserved for authorized all-resource access."""
        ...

    def get(self, dataset_id: str, *, for_update: bool = False) -> DatasetRecord | None: ...

    def find_by_key(self, key: str, *, for_update: bool = False) -> DatasetRecord | None: ...

    def list_all(self) -> list[DatasetRecord]: ...

    def mappings_using(self, source_dataset_id: str) -> list[DatasetRecord]: ...

    def mapping_usage_count(self, source_dataset_id: str) -> int: ...

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

    def list_for_datasets(
        self, dataset_ids: tuple[str, ...]
    ) -> dict[str, list[DatasetFieldRecord]]: ...

    def replace_order(self, dataset_id: str, field_ids: tuple[str, ...]) -> None: ...

    def rename_key(self, dataset_id: str, field_id: str, key: str) -> DatasetFieldRecord: ...

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

    def rename_deleted_field_key(self, dataset_id: str, field_id: str, key: str) -> None: ...

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

    def replace_values(
        self,
        record: DatasetRecordRow,
        values: tuple[RecordValue, ...],
        *,
        field_ids: tuple[str, ...] | None = None,
    ) -> None: ...

    def delete_if_revision(
        self, dataset_id: str, record_id: str, expected_revision: int
    ) -> bool: ...

    def current_revision(self, dataset_id: str, record_id: str) -> int | None: ...

    def add_values(self, record: DatasetRecordRow, values: tuple[RecordValue, ...]) -> None: ...

    def add_values_by_id(
        self, dataset_id: str, record_id: str, values: tuple[RecordValue, ...]
    ) -> None: ...

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


class OverviewOperations(Protocol):
    def recent_ids(self, user_id: str) -> tuple[str, ...]: ...

    def record_visit(self, user_id: str, dataset_id: str, now: datetime) -> None: ...

    def version_fields(self, version_ids): ...

    def draft_counts(self, dataset_ids) -> dict[str, tuple[int, int]]: ...

    def version_unmapped(self, version_ids) -> dict[str, int]: ...


class InstallationSettingsOperations(Protocol):
    def get_policy(self, *, for_update: bool = False) -> str: ...
    def change_policy(self, expected: str, policy: str) -> bool: ...


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

    def exportable_dataset_ids(self, user_id: str, team_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Datasets owned by the user's teams or explicitly granted to the user/teams."""
        ...

    def create(
        self, dataset_id: str, subject_type: str, subject_id: str
    ) -> DatasetExportAccessRecord: ...

    def delete(self, access: DatasetExportAccessRecord) -> None: ...

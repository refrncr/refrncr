"""Immutable, persistence-neutral REF-83 snapshot and lifecycle contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from refrncr.concept_contracts import ActiveConcept
from refrncr.ports import DatasetBusinessKey, DatasetFieldRecord, DatasetOverview, StoredRecord

if TYPE_CHECKING:
    from refrncr.services import RecordPage

APPLICATION_TIMEZONE = UTC
MAX_RELEASE_NOTE = 2000


@dataclass(frozen=True, slots=True)
class SnapshotField:
    id: str
    dataset_id: str
    key: str
    name: str
    data_type: str
    is_required: bool
    is_unique: bool
    display_position: int
    is_identifier: bool
    definition: str | None = None
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class SnapshotContent:
    name: str
    description: str
    fields: tuple[SnapshotField, ...]
    records: tuple[StoredRecord, ...]

    @property
    def identifier_keys(self):
        return tuple(sorted(field.key for field in self.fields if field.is_identifier))


@dataclass(frozen=True, slots=True)
class SummaryCounts:
    fields_added: int = 0
    fields_changed: int = 0
    fields_removed: int = 0
    records_added: int = 0
    records_changed: int = 0
    records_removed: int = 0


@dataclass(frozen=True, slots=True)
class PublishedVersion:
    id: str
    dataset_id: str
    number: int
    published_at: datetime
    publisher_id: str | None
    effective_date: date
    release_note: str
    name: str
    description: str
    field_count: int
    record_count: int
    summary: SummaryCounts
    cancelled_at: datetime | None = None
    cancelling_actor_id: str | None = None

    def __post_init__(self):
        # As for DatasetOverview, SQLite timestamps are stored as UTC without tzinfo.
        for attribute in ("published_at", "cancelled_at"):
            value = getattr(self, attribute)
            if value is not None:
                value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
                object.__setattr__(self, attribute, value)

    @property
    def system_published(self):
        return self.publisher_id is None


@dataclass(frozen=True, slots=True)
class VersionState:
    version: PublishedVersion
    status: str
    valid_until: date | None

    @property
    def end_date(self):
        return self.valid_until - timedelta(days=1) if self.valid_until else None


def version_states(versions, today):
    """Half-open intervals; higher version wins when effective dates are equal."""
    active = sorted(
        (v for v in versions if v.cancelled_at is None),
        key=lambda v: (v.effective_date, v.number),
    )
    boundaries = {
        version.id: active[i + 1].effective_date if i + 1 < len(active) else None
        for i, version in enumerate(active)
    }
    result = []
    for version in versions:
        end = boundaries.get(version.id)
        status = (
            "Cancelled"
            if version.cancelled_at is not None
            else "Scheduled"
            if version.effective_date > today
            else "Expired"
            if end is not None and end <= today
            else "Current"
        )
        result.append(VersionState(version, status, end))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class PublishedDataset:
    """Published content with current administrative context; never a mutable ORM row."""

    id: str
    key: str
    name: str
    description: str
    owner_team_id: str
    owner_team_name: str
    created_at: datetime
    updated_at: datetime
    content_updated_at: datetime
    revision: int
    version_id: str
    version_number: int
    owner_user_id: str = ""
    icon: str | None = None


@dataclass(frozen=True, slots=True)
class VersionRead:
    context: DatasetOverview
    fields: tuple[SnapshotField | DatasetFieldRecord, ...]
    identifier: DatasetBusinessKey
    page: RecordPage
    state: VersionState | None
    may_maintain: bool
    may_export: bool
    concept: ActiveConcept | None = None
    record_states: Mapping[str, str] | None = None
    deleted_count: int = 0
    baseline_fields: tuple[SnapshotField, ...] = ()
    record_labels: Mapping[str, str] | None = None
    search: str = ""
    deleted_fields: Mapping[str, tuple[SnapshotField, ...]] | None = None


@dataclass(frozen=True, slots=True)
class VersionHistory:
    context: DatasetOverview
    entries: tuple[VersionState, ...]
    publisher_names: Mapping[str, str]
    may_maintain: bool
    concept: ActiveConcept | None = None
    concept_summary: object = None

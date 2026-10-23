"""Immutable REF-84 projections. No ORM rows, arbitrary payloads or cached diff."""

from dataclasses import dataclass

from refrncr.change_status import STATUSES, ChangeStatus
from refrncr.version_contracts import SummaryCounts

PAGE_SIZE = 50
MAX_SEARCH = 200


@dataclass(frozen=True, slots=True)
class ReviewIdentity:
    dataset_id: str
    actor_id: str
    revision: int
    baseline_id: str | None
    baseline_number: int
    baseline_status: str
    concept_id: str = ""


@dataclass(frozen=True, slots=True)
class PropertyChange:
    name: str
    before: str | None
    after: str | None


@dataclass(frozen=True, slots=True)
class FieldChange:
    id: str
    name: str
    status: ChangeStatus
    properties: tuple[PropertyChange, ...]
    moved: bool = False
    datatype_changed: bool = False


@dataclass(frozen=True, slots=True)
class DiffSummary:
    identity: ReviewIdentity
    dataset_key: str
    dataset_name: str
    metadata: tuple[PropertyChange, ...]
    counts: SummaryCounts
    hierarchy_changes: tuple[FieldChange, ...] = ()
    hierarchy_errors: tuple[str, ...] = ()

    @property
    def has_changes(self):
        return bool(self.metadata or self.hierarchy_changes) or any(
            getattr(self.counts, name) for name in SummaryCounts.__dataclass_fields__
        )


@dataclass(frozen=True, slots=True)
class DiffCriteria:
    statuses: tuple[str | ChangeStatus, ...] = STATUSES
    search: str = ""
    anchor: str = ""
    backwards: bool = False


@dataclass(frozen=True, slots=True)
class RecordChange:
    id: str
    status: ChangeStatus
    before_identifier: str | None
    after_identifier: str | None
    changed_values: int
    sort_key: tuple


@dataclass(frozen=True, slots=True)
class RecordProjection:
    id: str
    status: ChangeStatus
    before_identifier: bytes | None
    after_identifier: bytes | None
    changed_values: int
    hierarchy: bool = False
    before_label: str | None = None
    after_label: str | None = None


@dataclass(frozen=True, slots=True)
class DiffPage:
    items: tuple
    total: int
    previous: str = ""
    next: str = ""
    status_counts: tuple[int, int, int] = (0, 0, 0)
    mapping_fields: tuple = ()
    mapping_values: tuple = ()


@dataclass(frozen=True, slots=True)
class ValueChange:
    field_id: str
    field_name: str
    before_type: str | None
    after_type: str | None
    before: str | None
    after: str | None


@dataclass(frozen=True, slots=True)
class RecordDetail:
    record: RecordChange
    values: tuple[ValueChange, ...]
    page: int
    total: int

    @property
    def has_next(self):
        return self.page * PAGE_SIZE < self.total

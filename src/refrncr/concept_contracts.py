"""Explicit active-concept identity; content remains in the existing working tables."""

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class ActiveConcept:
    id: str
    dataset_id: str
    base_version_id: str | None
    creator_id: str | None
    created_at: datetime
    updated_at: datetime
    start_revision: int

    def __post_init__(self):
        for name in ("created_at", "updated_at"):
            value = getattr(self, name)
            object.__setattr__(
                self,
                name,
                value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC),
            )


@dataclass(frozen=True, slots=True)
class ConceptContext:
    concept: ActiveConcept
    baseline: object
    summary: object
    field_changes: tuple
    creator_name: str

    @property
    def stale(self):
        return self.baseline is not None and self.baseline.status == "Cancelled"

    @property
    def has_changes(self):
        return self.summary.has_changes

    @property
    def can_review(self):
        return self.has_changes and not self.stale

    @property
    def may_publish(self):
        return self.can_review and (self.baseline is None or self.baseline.status != "Scheduled")

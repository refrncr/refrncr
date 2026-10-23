"""Explicit active-concept identity; content remains in the existing working tables."""

from dataclasses import dataclass
from datetime import datetime

DISCARD_UNAVAILABLE = (
    "This Concept contains the Dataset’s first unpublished version. "
    "There is no Current version to return to. Publish it first or delete the Dataset."
)


@dataclass(frozen=True, slots=True)
class DiscardIdentity:
    dataset_id: str
    concept_id: str
    revision: int
    start_revision: int
    concept_updated_at: str
    actor_id: str
    owner_team_id: str
    current_id: str


@dataclass(frozen=True, slots=True)
class DiscardReview:
    identity: DiscardIdentity
    dataset_name: str
    counts: object
    metadata_changes: int
    hierarchy_changes: int = 0


@dataclass(frozen=True, slots=True)
class ActiveConcept:
    id: str
    dataset_id: str
    base_version_id: str | None
    creator_id: str | None
    created_at: datetime
    updated_at: datetime
    start_revision: int
    records_mutated: bool | None = False
    import_result: dict | None = None
    source_update_notes: list[str] | None = None


@dataclass(frozen=True, slots=True)
class ConceptContext:
    concept: ActiveConcept
    baseline: object
    summary: object
    field_changes: tuple
    creator_name: str
    has_current: bool = False

    @property
    def discard_unavailable(self):
        return None if self.has_current else DISCARD_UNAVAILABLE

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
        return (
            self.can_review
            and not self.summary.hierarchy_errors
            and (self.baseline is None or self.baseline.status != "Scheduled")
        )

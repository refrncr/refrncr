"""Persistence-neutral port contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class AuditEntry:
    actor_user_id: str | None
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

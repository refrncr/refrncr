"""Canonical internal change statuses and their user-facing presentation."""

from enum import StrEnum


class ChangeStatus(StrEnum):
    """Persisted values used to classify draft-to-baseline changes."""

    ADDED = "Added"
    CHANGED = "Changed"
    REMOVED = "Removed"


LEGACY_STATUS_ALIASES = {"Edited": ChangeStatus.CHANGED}
UI_STATUS_ALIASES = {
    "Added": ChangeStatus.ADDED,
    "Changed": ChangeStatus.CHANGED,
    "Deleted": ChangeStatus.REMOVED,
}
RECORD_STATUS_FILTERS = {
    "added": ChangeStatus.ADDED,
    "edited": ChangeStatus.CHANGED,
    "deleted": ChangeStatus.REMOVED,
}
STATUSES = tuple(ChangeStatus)


def change_status(value: str | ChangeStatus) -> ChangeStatus:
    """Resolve a stored change status, including the historical Edited alias."""
    if isinstance(value, ChangeStatus):
        return value
    if value in LEGACY_STATUS_ALIASES:
        return LEGACY_STATUS_ALIASES[value]
    if value in UI_STATUS_ALIASES:
        return UI_STATUS_ALIASES[value]
    return ChangeStatus(value)

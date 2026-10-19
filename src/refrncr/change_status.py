"""Canonical internal change statuses and their user-facing presentation."""

from dataclasses import dataclass
from enum import StrEnum


class ChangeStatus(StrEnum):
    """Persisted values used to classify draft-to-baseline changes."""

    ADDED = "Added"
    CHANGED = "Changed"
    REMOVED = "Removed"


@dataclass(frozen=True, slots=True)
class StatusPresentation:
    label: str
    badge_variant: str


STATUS_PRESENTATION = {
    ChangeStatus.ADDED: StatusPresentation("Added", "badge-draft"),
    ChangeStatus.CHANGED: StatusPresentation("Changed", "badge-draft"),
    ChangeStatus.REMOVED: StatusPresentation("Deleted", "badge-removed"),
}

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
DISPLAY_STATUSES = tuple(STATUS_PRESENTATION[status].label for status in ChangeStatus)


def change_status(value: str | ChangeStatus) -> ChangeStatus:
    """Resolve a stored change status, including the historical Edited alias."""
    if isinstance(value, ChangeStatus):
        return value
    if value in LEGACY_STATUS_ALIASES:
        return LEGACY_STATUS_ALIASES[value]
    if value in UI_STATUS_ALIASES:
        return UI_STATUS_ALIASES[value]
    return ChangeStatus(value)


def status_label(value: str | ChangeStatus) -> str:
    """Return the user-facing label for a change status; leave unrelated labels alone."""
    if value in LEGACY_STATUS_ALIASES:
        value = LEGACY_STATUS_ALIASES[value]
    elif value in UI_STATUS_ALIASES:
        value = UI_STATUS_ALIASES[value]
    try:
        return STATUS_PRESENTATION[ChangeStatus(value)].label
    except ValueError:
        return value


def status_badge_variant(value: str | ChangeStatus) -> str:
    """Return the design-system badge variant for a change status, if applicable."""
    if value in LEGACY_STATUS_ALIASES:
        value = LEGACY_STATUS_ALIASES[value]
    elif value in UI_STATUS_ALIASES:
        value = UI_STATUS_ALIASES[value]
    try:
        return STATUS_PRESENTATION[ChangeStatus(value)].badge_variant
    except ValueError:
        return ""

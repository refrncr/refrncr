"""Canonical user-facing labels and variants for application statuses and dataset types."""

from collections.abc import Iterable
from dataclasses import dataclass

from refrncr.change_status import (
    LEGACY_STATUS_ALIASES,
    UI_STATUS_ALIASES,
    ChangeStatus,
)


@dataclass(frozen=True, slots=True)
class StatusPresentation:
    label: str
    badge_variant: str


STATUS_PRESENTATION = {
    ChangeStatus.ADDED: StatusPresentation("Added", "badge-draft"),
    ChangeStatus.CHANGED: StatusPresentation("Changed", "badge-draft"),
    ChangeStatus.REMOVED: StatusPresentation("Deleted", "badge-removed"),
    "Current": StatusPresentation("Current", "badge-published"),
    "Published": StatusPresentation("Published", "badge-published"),
    "Scheduled": StatusPresentation("Scheduled", "badge-info"),
    "Cancelled": StatusPresentation("Cancelled", "badge-warning"),
    "Draft": StatusPresentation("Draft", "badge-draft"),
    "Stale": StatusPresentation("Stale", "badge-removed"),
    "Error": StatusPresentation("Error", "badge-error"),
    "Inactive": StatusPresentation("Inactive", "badge-warning"),
    "Deleted": StatusPresentation("Deleted", "badge-removed"),
}

DISPLAY_STATUSES = tuple(STATUS_PRESENTATION[status].label for status in ChangeStatus)
DATASET_KIND_LABELS = {
    "reference": "Reference",
    "mapping": "Mapping",
    "hierarchy": "Hierarchy",
}


def status_label(value: str | ChangeStatus) -> str:
    """Return a visible status label, preserving legacy aliases and unrelated text."""
    if value in LEGACY_STATUS_ALIASES:
        value = LEGACY_STATUS_ALIASES[value]
    elif value in UI_STATUS_ALIASES:
        value = UI_STATUS_ALIASES[value]
    try:
        return STATUS_PRESENTATION[ChangeStatus(value)].label
    except (ValueError, KeyError):
        return STATUS_PRESENTATION.get(value, StatusPresentation(str(value), "")).label


def status_badge_variant(value: str | ChangeStatus) -> str:
    """Return the shared badge class for known statuses; unknown labels stay neutral."""
    label = status_label(value)
    presentation = STATUS_PRESENTATION.get(label)
    if presentation is not None:
        return presentation.badge_variant
    try:
        return STATUS_PRESENTATION[ChangeStatus(value)].badge_variant
    except (ValueError, KeyError):
        return ""


def dataset_kind_label(kind: str) -> str:
    """Return the canonical user-facing label for a supported dataset kind."""
    try:
        return DATASET_KIND_LABELS[kind]
    except KeyError:
        raise ValueError(f"Unknown dataset kind: {kind}") from None


def record_label(parts: Iterable[str | None], *, sentence: bool = False) -> str:
    """Format identifier components consistently for labels and sentences."""
    values = tuple(parts)
    if not values:
        return "this record" if sentence else "—"
    if sentence and any(value is None or value == "" for value in values):
        return "this record"
    return " · ".join(value if value not in (None, "") else "—" for value in values)


def pluralize(label: str, count: int) -> str:
    """Return a simple English singular/plural label for the UI."""
    if count != 1:
        return label
    if label.endswith("ies"):
        return f"{label[:-3]}y"
    return label.removesuffix("s")

from refrncr.change_status import (
    DISPLAY_STATUSES,
    STATUS_PRESENTATION,
    ChangeStatus,
    change_status,
    status_badge_variant,
    status_label,
)


def test_change_status_has_one_visible_label_and_badge_mapping():
    assert DISPLAY_STATUSES == ("Added", "Changed", "Deleted")
    assert status_label(ChangeStatus.ADDED) == "Added"
    assert status_label(ChangeStatus.CHANGED) == "Changed"
    assert status_label(ChangeStatus.REMOVED) == "Deleted"
    assert status_badge_variant(ChangeStatus.ADDED) == "badge-draft"
    assert status_badge_variant(ChangeStatus.CHANGED) == "badge-draft"
    assert status_badge_variant(ChangeStatus.REMOVED) == "badge-removed"
    assert STATUS_PRESENTATION[ChangeStatus.REMOVED].label == "Deleted"


def test_legacy_status_aliases_resolve_to_the_canonical_changed_status():
    assert change_status("Edited") is ChangeStatus.CHANGED
    assert status_label("Edited") == "Changed"
    assert status_label("Unrelated") == "Unrelated"

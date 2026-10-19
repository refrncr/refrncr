import subprocess
import sys

from refrncr.change_status import ChangeStatus, change_status
from refrncr.presentation import (
    DISPLAY_STATUSES,
    STATUS_PRESENTATION,
    dataset_kind_label,
    pluralize,
    record_label,
    status_badge_variant,
    status_label,
)


def test_presentation_module_imports_directly_in_a_fresh_python_process():
    subprocess.run(
        [sys.executable, "-c", "import refrncr.presentation"],
        check=True,
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
    assert status_badge_variant("Current") == "badge-published"
    assert status_badge_variant("Scheduled") == "badge-info"
    assert status_badge_variant("Draft") == "badge-draft"
    assert status_badge_variant("Stale") == "badge-removed"
    assert status_badge_variant("Error") == "badge-error"
    assert status_badge_variant("Unrelated") == ""
    assert dataset_kind_label("reference") == "Reference"
    assert dataset_kind_label("mapping") == "Mapping"
    assert dataset_kind_label("hierarchy") == "Hierarchy"


def test_legacy_status_aliases_resolve_to_the_canonical_changed_status():
    assert change_status("Edited") is ChangeStatus.CHANGED
    assert status_label("Edited") == "Changed"
    assert status_label("Unrelated") == "Unrelated"


def test_record_labels_use_one_separator_and_distinguish_sentence_fallback():
    assert record_label(("A", "B")) == "A · B"
    assert record_label(("A", None)) == "A · —"
    assert record_label(()) == "—"
    assert record_label(("A", None), sentence=True) == "this record"
    assert record_label((), sentence=True) == "this record"


def test_pluralize_is_shared_for_regular_and_ies_nouns():
    assert pluralize("records", 1) == "record"
    assert pluralize("records", 2) == "records"
    assert pluralize("categories", 1) == "category"
    assert pluralize("categories", 2) == "categories"

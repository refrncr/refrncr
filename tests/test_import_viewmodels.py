from types import SimpleNamespace

from refrncr.web.viewmodels.imports import import_wizard_status


def state(*, imported_count=None, mapping_ready=False, validation_valid=False):
    return SimpleNamespace(
        imported_count=imported_count,
        mapping_ready=mapping_ready,
        validation_valid=validation_valid,
    )


def source(*, kind="csv", worksheets=("Sheet",), headers=("code",)):
    return SimpleNamespace(kind=kind, worksheets=worksheets, headers=headers)


def test_new_import_starts_at_upload_with_no_reachable_steps():
    status = import_wizard_status(None, None, 1)

    assert [(item.number, item.done, item.reachable) for item in status.steps] == [
        (1, False, False),
        (2, False, False),
        (3, False, False),
        (4, False, False),
    ]
    assert status.back_step == 0


def test_automatic_source_skips_worksheet_and_allows_mapping_navigation():
    status = import_wizard_status(state(), source(), 3)

    assert status.automatic is True
    assert [(item.done, item.reachable) for item in status.steps] == [
        (True, True),
        (True, False),
        (False, False),
        (False, False),
    ]
    assert status.back_step == 1


def test_workbook_keeps_worksheet_step_reachable():
    status = import_wizard_status(
        state(mapping_ready=True),
        source(kind="xlsx", worksheets=("First", "Second")),
        3,
    )

    assert status.automatic is False
    assert status.steps[1].reachable is True
    assert status.back_step == 2


def test_preview_returns_to_mapping_and_marks_validation_complete():
    status = import_wizard_status(state(mapping_ready=True, validation_valid=True), source(), 4)

    assert [item.done for item in status.steps] == [True, True, True, True]
    assert status.steps[2].reachable is True
    assert status.back_step == 3


def test_completed_import_marks_every_step_done_and_none_reachable():
    status = import_wizard_status(
        state(imported_count=4, mapping_ready=True, validation_valid=True), source(), 4
    )

    assert status.success is True
    assert all(item.done for item in status.steps)
    assert not any(item.reachable for item in status.steps)

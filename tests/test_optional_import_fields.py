"""Partial field mappings retain draft values within a complete record-set import."""

import pytest
from sqlalchemy import select, text
from support import publish, setup, snapshot

from refrncr.models import AuditEvent, DatasetRecord
from refrncr.record_import import ImportValidationError


def stage(c, source, mapping):
    state, _, _ = c.s.record_imports.begin(
        c.a.id, "optional-fields", c.d.key, "records.csv", source, mode="new_version"
    )
    _, _, assistance = c.s.record_imports.mapping_assistance(
        c.a.id, state.session_id, c.d.key, state.token, mapping
    )
    result = c.s.record_imports.preview(c.a.id, state.session_id, c.d.key, state.token, mapping)
    state = c.s.record_imports.state(c.a.id, state.session_id, c.d.key, state.token)
    return state, result, assistance


def confirm(c, state, **kwargs):
    return c.s.record_imports.confirm(
        c.a.id, state.session_id, c.d.key, state.token, confirmation=state.confirmation, **kwargs
    )


def current(c):
    return c.s.dataset_records.get_record_for_edit(c.a.id, c.d.key, c.record.id)


def values(record):
    return {value.field_key: value.value for value in record.values}


@pytest.mark.parametrize("earlier_edit", [False, True])
def test_changed_record_preserves_unmapped_storage_and_draft_values(database_context, earlier_edit):
    c = database_context
    setup(c)
    field = c.s.dataset_fields.create_field(c.a.id, c.d.key, "note", "Note", "string")
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Café", "note": "Original"}
    )
    if earlier_edit:
        c.s.dataset_records.update_record(
            c.a.id,
            c.d.key,
            c.record.id,
            2,
            {"code": "a", "label": "Café", "note": "Earlier draft edit"},
        )
    else:
        publish(c)
        c.service.create_concept(c.a.id, c.d.id)
    before = current(c)
    # A database guard verifies the retention contract without inspecting source code.
    with c.engine.begin() as connection:
        if c.engine.dialect.name == "sqlite":
            for operation in ("DELETE", "UPDATE"):
                connection.execute(
                    text(
                        f"CREATE TRIGGER protect_note_{operation.lower()} BEFORE {operation} "
                        f"ON dataset_record_values WHEN OLD.field_id = '{field.id}' "
                        "BEGIN SELECT RAISE(ABORT, 'retained value was written'); END"
                    )
                )
        else:
            connection.execute(
                text(
                    "CREATE FUNCTION protect_note() RETURNS trigger LANGUAGE plpgsql AS $$ "
                    f"BEGIN IF OLD.field_id = '{field.id}' THEN "
                    "RAISE EXCEPTION 'retained value was written'; END IF; RETURN OLD; END $$"
                )
            )
            connection.execute(
                text(
                    "CREATE TRIGGER protect_note BEFORE DELETE OR UPDATE ON dataset_record_values "
                    "FOR EACH ROW EXECUTE FUNCTION protect_note()"
                )
            )
    state, result, assistance = stage(c, b"code,label\na,Changed\nb,New\n", ("code", "label"))
    assert result.valid and not assistance.errors
    assert assistance.required_keys == ("code",)
    assert assistance.completeness.required_total == assistance.completeness.required_mapped == 1
    assert result.counts["Changed"] == result.counts["Added"] == 1
    changed = next(item for item in result.differences if item["status"] == "Changed")
    assert changed["changes"] == (("Label", "Café", "Changed"),)
    assert ("Note", values(before)["note"]) in changed["current_values"]
    confirm(c, state)
    assert values(current(c)) == {"code": "a", "label": "Changed", "note": values(before)["note"]}
    assert current(c).revision == before.revision + 1
    added = next(
        r for r in c.s.dataset_records.list_records(c.a.id, c.d.key).records if r.id != c.record.id
    )
    assert values(added) == {"code": "b", "label": "New"}
    with c.sessions() as session:
        event = session.scalars(
            select(AuditEvent).where(AuditEvent.action == "dataset.new_version_imported")
        ).one()
        assert event.details["unmapped_fields"] == ["note"]


def test_omitted_fields_do_not_change_record_or_revision(database_context):
    c = database_context
    setup(c)
    before = current(c)
    state, result, _ = stage(c, b"code,ignored\na,x\n", ("code", None))
    assert result.valid
    assert result.counts["Unchanged"] == 1
    assert result.counts["Changed"] == 0
    assert result.differences == ()
    confirm(c, state)
    assert current(c) == before


def test_mapped_blank_clears_optional_value(database_context):
    c = database_context
    setup(c)
    state, result, _ = stage(c, b"code,label\na,\n", ("code", "label"))
    assert result.valid and result.counts["Changed"] == 1
    assert result.differences[0]["changes"] == (("Label", "Café", None),)
    confirm(c, state)
    assert values(current(c)) == {"code": "a"}


@pytest.mark.parametrize("composite", [False, True])
def test_identifier_mapping_and_nonempty_cells_remain_required(database_context, composite):
    c = database_context
    setup(c, populated=False)
    if composite:
        c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Label", "string", True, False)
        c.s.dataset_business_keys.set_business_key(c.a.id, c.d.key, ("code", "label"))
    mapping = ("code", None) if composite else ("label", None)
    state, result, assistance = stage(c, b"value,ignored\na,x\n", mapping)
    assert not result.valid
    missing = "label" if composite else "code"
    assert any(
        issue.field_key == missing and issue.code == "required_field_unmapped"
        for issue in assistance.errors
    )
    assert set(assistance.required_keys) == ({"code", "label"} if composite else {"code"})
    c.s.record_imports.cancel(c.a.id, state.session_id, c.d.key, state.token)
    _, result, _ = stage(c, b"code,label\n,Cafe\n", ("code", "label"))
    assert not result.valid
    assert any(
        issue.row == 2 and issue.field == "Code" and "required" in issue.message
        for issue in result.errors
    )


def test_blank_required_nonidentifier_is_a_row_error(database_context):
    c = database_context
    setup(c)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Label", "string", True, False)
    _, result, assistance = stage(c, b"code,label\na,\n", ("code", "label"))
    assert not assistance.errors
    assert assistance.completeness.required_total == 2
    assert not result.valid
    assert any(
        issue.row == 2 and issue.field == "Label" and "required" in issue.message
        for issue in result.errors
    )


def test_deleted_identifier_is_added_without_retained_values(database_context):
    c = database_context
    setup(c)
    c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, 1)
    state, result, _ = stage(c, b"code,ignored\na,x\n", ("code", None))
    assert result.valid and result.counts["Added"] == 1
    assert result.counts["Changed"] == result.counts["Deleted"] == 0
    confirm(c, state)
    view = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    added = next(r for r in view.page.records if r.id != c.record.id)
    assert values(added) == {"code": "a"}
    with c.sessions() as session:
        assert session.get(DatasetRecord, c.record.id).deleted_content is not None


def test_unmapped_unique_values_survive_changed_rows_and_multiple_new_rows(database_context):
    c = database_context
    setup(c)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Label", "string", False, True)
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "note", "Note", "string")
    other = c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "b", "label": "Other"})
    state, result, _ = stage(
        c, b"code,note\na,Changed\nb,Changed\nc,New\nd,New\n", ("code", "note")
    )
    assert result.valid and result.counts["Changed"] == result.counts["Added"] == 2
    confirm(c, state)
    assert values(current(c))["label"] == "Café"
    assert (
        values(c.s.dataset_records.get_record_for_edit(c.a.id, c.d.key, other.id))["label"]
        == "Other"
    )
    active = c.s.dataset_records.list_records(c.a.id, c.d.key).records
    assert all("label" not in values(r) for r in active if values(r)["code"] in {"c", "d"})


def test_omitted_value_edit_invalidates_preview(database_context):
    c = database_context
    setup(c)
    state, result, _ = stage(c, b"code,ignored\na,x\n", ("code", None))
    assert result.valid
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "Edited after preview"}
    )
    before = snapshot(c)
    with pytest.raises(ImportValidationError):
        confirm(c, state)
    assert snapshot(c) == before


def test_missing_record_requires_acknowledgement_and_keeps_deleted_values(database_context):
    c = database_context
    setup(c)
    state, result, _ = stage(c, b"code,ignored\nb,x\n", ("code", None))
    assert result.valid and result.counts["Deleted"] == result.counts["Added"] == 1
    before = snapshot(c)
    with pytest.raises(ImportValidationError, match="exact number"):
        confirm(c, state)
    assert snapshot(c) == before
    confirm(c, state, acknowledge_deletions=True)
    view = c.service.read(c.a.id, c.d.key, prefer_concept=True)
    deleted = next(r for r in view.page.records if r.id == c.record.id)
    assert values(deleted)["label"] == "Café"
    assert view.record_states[deleted.id] == "Deleted"

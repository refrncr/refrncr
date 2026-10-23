"""Reconcile pinned mapping sources inside the caller's Dataset transaction."""

from refrncr.ports import RecordValue
from refrncr.record_identity import encode_record_identifier
from refrncr.services import ConflictError, ValidationError


class SourceUpdateBlocked(ValidationError):
    """The published source cannot be used; explain this in the mapping notice."""


def source_update_blocker(dataset, source, fields, identifier_type):
    identifier = next((field for field in fields if field.key == dataset.source_field_key), None)
    if (
        identifier is None
        or not identifier.is_required
        or not (
            identifier.is_unique
            or (identifier.is_identifier and sum(field.is_identifier for field in fields) == 1)
        )
    ):
        return (
            f"This mapping can't move to V{source.number} because {dataset.source_field_key} "
            f"is no longer a required, unique field in {source.name} V{source.number}. "
            f"Ask the owner of {source.name} to restore it, or create a new mapping."
        )
    if identifier.data_type != identifier_type:
        return (
            f"This mapping can't move to V{source.number} because the datatype of "
            f"{dataset.source_field_key} changed. Ask the owner of {source.name} "
            "to restore it, or create a new mapping."
        )
    return None


def mapping_states(fields, records, removed=()):
    targets = {field.id for field in fields if field.mapping_role == "mapping_target"}
    return {
        **{
            record.id: "Unmapped"
            for record in records
            if not any(value.field_id in targets for value in record.values)
        },
        **{record.id: "Removed from source" for record in removed},
    }


def reconcile_source(uow, dataset, source):
    fields = uow.dataset_fields.list_for_dataset(dataset.id)
    source_fields = {field.key: field for field in uow.versions.fields(source)}
    identifier_type = next(f.data_type for f in fields if f.mapping_role == "source_identifier")
    if reason := source_update_blocker(
        dataset, source, tuple(source_fields.values()), identifier_type
    ):
        raise SourceUpdateBlocked(reason)
    copied = [field for field in fields if field.source_field_key]
    removed_fields = []
    notes = []
    for field in copied:
        incoming = source_fields.get(field.source_field_key)
        if incoming is None or incoming.data_type != field.data_type:
            reason = (
                f"it no longer exists in {source.name} V{source.number}"
                if incoming is None
                else f"its datatype changed in {source.name} V{source.number}"
            )
            notes.append(f"Source field {field.source_field_key} was removed because {reason}.")
            removed_fields.append(field)
    copied = [field for field in copied if field not in removed_fields]
    fields = [field for field in fields if field not in removed_fields]
    identifier = source_fields[dataset.source_field_key]
    if not uow.version_diff.validate_snapshot(source):
        raise ValidationError("Invalid published source snapshot")

    def key(record):
        return next(v.canonical_key for v in record.values if v.field_key == identifier.key)

    incoming = {key(record): record for record in uow.versions.iterate_records(source)}
    active = {key(record): record for record in uow.dataset_records.iterate_all(dataset.id)}
    deleted = {key(record): record for record, _ in uow.dataset_records.deleted_records(dataset.id)}
    target_fields = {field.id: field for field in fields if field.mapping_role == "mapping_target"}

    def targets(record):
        # Retained tombstones may predate target field deletion or datatype edits.
        return tuple(
            RecordValue(
                field.id,
                field.key,
                field.data_type,
                value.value,
                value.canonical_key,
                field.is_unique,
            )
            for value in record.values
            if (field := target_fields.get(value.field_id)) and field.data_type == value.data_type
        )

    for source_key, record in active.items():
        if source_key not in incoming:
            if not uow.dataset_records.delete_if_revision(dataset.id, record.id, record.revision):
                raise ConflictError("Mapping changed. Reload and try again.")
        else:
            # Release source unique values before copying the entire new snapshot, so
            # swaps of unique context values do not create transient collisions.
            uow.dataset_records.replace_values(record, targets(record))
    uow.flush()
    for source_key, source_record in incoming.items():
        encoded = encode_record_identifier(
            {identifier.key: source_key}, expected_keys=(identifier.key,)
        )
        record = active.get(source_key) or deleted.get(source_key)
        retained = targets(record) if record else ()
        if record is None:
            record = uow.dataset_records.create(dataset_id=dataset.id, identifier_key=encoded)
            uow.flush()
        else:
            operation = (
                uow.dataset_records.update_if_revision
                if source_key in active
                else uow.dataset_records.restore_if_revision
            )
            if not operation(dataset.id, record.id, record.revision, encoded):
                raise ConflictError("Mapping changed. Reload and try again.")
        source_values = {value.field_key: value for value in source_record.values}
        values = tuple(
            RecordValue(
                field.id,
                field.key,
                field.data_type,
                value.value,
                value.canonical_key,
                field.is_unique,
            )
            for field in copied
            if (value := source_values.get(field.source_field_key)) is not None
        )
        uow.dataset_records.replace_values(record, retained + values)
    for field in copied:
        field.source_field_id = source_fields[field.source_field_key].id
    uow.flush()
    # Active values have now been replaced or moved into retained tombstones.
    for field in removed_fields:
        uow.dataset_fields.delete(field)
    if removed_fields:
        uow.dataset_fields.replace_order(dataset.id, tuple(field.id for field in fields))
    uow.flush()
    return notes

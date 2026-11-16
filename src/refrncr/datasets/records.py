from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from refrncr.audit import record_audit
from refrncr.datasets import export as dataset_export
from refrncr.datasets.datatypes import get_datatype
from refrncr.datasets.deletion import version_reads as _version_reads
from refrncr.datasets.errors import (
    DatasetNotFoundError,
    DatasetValidationError,
    RecordConflictError,
    RecordValidationError,
    StaleRecordError,
)
from refrncr.datasets.fields import DatasetFieldService
from refrncr.datasets.record_identity import encode_record_identifier
from refrncr.datasets.shared import (
    canonicalize_record_value,
)
from refrncr.datasets.shared import (
    require_identifier_type as _require_identifier_type,
)
from refrncr.domain import advance_concept, require_concept
from refrncr.errors import ValidationError
from refrncr.ports import (
    DatasetBusinessKey,
    DatasetRecord,
    PersistenceConflictError,
    RecordValue,
    StoredRecord,
    UnitOfWork,
    UnitOfWorkFactory,
)

if TYPE_CHECKING:
    from refrncr.authorization import AuthorizationService
from refrncr.versions.version_contracts import mapping_incomplete_message

_EXPECTED_REVISION_PATTERN = re.compile(r"[1-9][0-9]*")
MAX_RECORD_REVISION = 2_147_483_647


def parse_expected_revision(raw: str) -> int:
    if not isinstance(raw, str) or _EXPECTED_REVISION_PATTERN.fullmatch(raw) is None:
        raise DatasetValidationError("Expected revision must be a canonical positive integer")
    revision = int(raw)
    if revision > MAX_RECORD_REVISION:
        raise DatasetValidationError("Expected revision is out of range")
    return revision


@dataclass(frozen=True, slots=True)
class RecordPage:
    records: tuple[StoredRecord, ...]
    page: int
    page_size: int
    total: int


class DatasetRecordService:
    """Authorized current-record operations scoped through a parent Dataset."""

    PAGE_SIZE = 50

    def __init__(
        self, uow: UnitOfWorkFactory, authorization: AuthorizationService, *, versions=None
    ) -> None:
        self.uow = uow
        self.authorization = authorization
        self.versions = versions

    def create_record(
        self, actor_id: str, dataset_key: str, submitted: dict[str, str]
    ) -> StoredRecord:
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_key, for_update=True)
            self._require_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            if dataset.kind == "mapping" and dataset.mapping_mode == "linked":
                raise DatasetValidationError(
                    "Linked mapping records are populated from the source dataset"
                )
            fields = uow.dataset_fields.list_for_dataset(dataset.id)
            if dataset.kind == "mapping":
                if message := mapping_incomplete_message(fields):
                    raise DatasetValidationError(message)
            identifier = uow.dataset_business_keys.get(dataset.id)
            values, encoded_identifier = self._validated_values(fields, identifier, submitted)
            unique_values = tuple(
                (value.field_id, value.canonical_key) for value in values if value.is_unique
            )
            existing_unique = uow.dataset_records.existing_unique_value_keys(
                dataset.id, unique_values
            )
            if existing_unique:
                field_by_id = {field.id: field for field in fields}
                conflicting_field = field_by_id[next(iter(existing_unique))[0]]
                raise RecordConflictError(
                    f"{conflicting_field.name} must be unique; this value already exists",
                    conflicting_field.key,
                )
            record = uow.dataset_records.create(
                dataset_id=dataset.id, identifier_key=encoded_identifier
            )
            try:
                uow.flush()
                uow.dataset_records.add_values(record, tuple(values))
                uow.flush()
            except PersistenceConflictError as error:
                if error.constraint == "uq_dataset_records_dataset_identifier":
                    raise RecordConflictError("Record identifier already exists") from error
                if error.constraint == "uq_dataset_record_values_unique_value":
                    raise RecordConflictError("A unique field value already exists") from error
                raise
            uow.concepts.record_mutation(dataset.id)
            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset_record",
                object_id=record.id,
                action="created",
            )
            return StoredRecord(record.id, dataset.id, tuple(values), 1)

    def get_record_for_edit(self, actor_id: str, dataset_key: str, record_id: str) -> StoredRecord:
        dataset = self._find_dataset(actor_id, dataset_key, "can_maintain_dataset")
        with self.uow() as uow:
            record = uow.dataset_records.get(dataset.id, record_id)
        if record is None:
            raise DatasetNotFoundError("Record not found")
        return record

    def update_record(
        self,
        actor_id: str,
        dataset_key: str,
        record_id: str,
        expected_revision: int,
        submitted: dict[str, str],
    ) -> StoredRecord:
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise DatasetValidationError("Expected revision must be a positive integer")
        if not 1 <= expected_revision <= MAX_RECORD_REVISION:
            raise DatasetValidationError("Expected revision is out of range")
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_key, for_update=True)
            self._require_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            existing = uow.dataset_records.get(dataset.id, record_id)
            if existing is None:
                raise DatasetNotFoundError("Record not found")
            fields = uow.dataset_fields.list_for_dataset(dataset.id)
            for field in fields:
                if not field.source_field_key:
                    continue
                existing_value = next(
                    (value for value in existing.values if value.field_id == field.id), None
                )
                submitted.pop(field.key, None)
                if existing_value is not None:
                    submitted[field.key] = get_datatype(field.data_type).text(existing_value.value)
            identifier = uow.dataset_business_keys.get(dataset.id)
            values, encoded_identifier = self._validated_values(fields, identifier, submitted)
            # Check stale input before accepting a normalized no-op. The Dataset
            # lock serializes supported writes throughout this comparison.
            if existing.revision != expected_revision:
                raise StaleRecordError("Record changed since this form was opened")
            if {value.field_id: value.canonical_key for value in existing.values} == {
                value.field_id: value.canonical_key for value in values
            }:
                return existing
            try:
                updated = uow.dataset_records.update_if_revision(
                    dataset.id, record_id, expected_revision, encoded_identifier
                )
                if not updated:
                    self._raise_missing_or_stale(uow, dataset.id, record_id)
                uow.dataset_records.replace_values(existing, values)
                uow.flush()
            except PersistenceConflictError as error:
                self._raise_record_conflict(error)
            uow.concepts.record_mutation(dataset.id)
            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset_record",
                object_id=record_id,
                action="updated",
            )
            return StoredRecord(record_id, dataset.id, values, expected_revision + 1)

    def delete_record(
        self, actor_id: str, dataset_key: str, record_id: str, expected_revision: int
    ) -> None:
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise DatasetValidationError("Expected revision must be a positive integer")
        if not 1 <= expected_revision < MAX_RECORD_REVISION:
            raise DatasetValidationError("Expected revision is out of range")
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_key, for_update=True)
            self._require_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            if dataset.kind == "mapping":
                raise DatasetValidationError(
                    "Source records are read-only; edit the target values instead."
                )
            if not uow.dataset_records.delete_if_revision(dataset.id, record_id, expected_revision):
                self._raise_missing_or_stale(uow, dataset.id, record_id)
            uow.concepts.record_mutation(dataset.id)
            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset_record",
                object_id=record_id,
                action="deleted",
            )

    def restore_record(self, actor_id, dataset_key, record_id, expected_revision, *, concept_id):
        if type(expected_revision) is not int or not 1 <= expected_revision < MAX_RECORD_REVISION:
            raise DatasetValidationError("Expected revision is out of range")
        key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(key, for_update=True)
            self._require_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            if dataset.kind == "mapping":
                raise DatasetValidationError(
                    "Source records can only be restored by updating the source version."
                )
            if require_concept(uow, dataset.id).id != concept_id:
                raise DatasetNotFoundError("Record not found")
            deleted = next(
                (
                    r
                    for r, _fields in uow.dataset_records.deleted_records(dataset.id)
                    if r.id == record_id
                ),
                None,
            )
            if deleted is None:
                raise DatasetNotFoundError("Record not found")
            if deleted.revision != expected_revision:
                raise StaleRecordError("Record changed since this form was opened")
            fields = uow.dataset_fields.list_for_dataset(dataset.id)
            by_id = {f.id: f for f in fields}
            if any(
                v.field_id not in by_id or by_id[v.field_id].data_type != v.data_type
                for v in deleted.values
            ):
                raise DatasetValidationError(
                    "The Field schema changed. This Record cannot be restored without changing its "
                    "retained values. Review its values before publishing or discarding "
                    "this Concept."
                )
            submitted = {
                v.field_key: get_datatype(v.data_type).text(v.value) for v in deleted.values
            }
            values, encoded = self._validated_values(
                fields, uow.dataset_business_keys.get(dataset.id), submitted
            )
            try:
                if not uow.dataset_records.restore_if_revision(
                    dataset.id, record_id, expected_revision, encoded
                ):
                    raise StaleRecordError("Record changed since this form was opened")
                uow.dataset_records.add_values(deleted, values)
                uow.flush()
            except PersistenceConflictError as error:
                self._raise_record_conflict(error)
            uow.concepts.record_mutation(dataset.id)
            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset_record",
                object_id=record_id,
                action="restored",
            )
            return StoredRecord(record_id, dataset.id, values, expected_revision + 1)

    @staticmethod
    def _raise_missing_or_stale(uow: UnitOfWork, dataset_id: str, record_id: str) -> None:
        if uow.dataset_records.current_revision(dataset_id, record_id) is None:
            raise DatasetNotFoundError("Record not found")
        raise StaleRecordError("Record changed since this form was opened")

    @staticmethod
    def _raise_record_conflict(error: PersistenceConflictError) -> None:
        if error.constraint == "uq_dataset_records_dataset_identifier":
            raise RecordConflictError("Record identifier already exists") from error
        if error.constraint == "uq_dataset_record_values_unique_value":
            raise RecordConflictError("A unique field value already exists") from error
        raise error

    @staticmethod
    def _validated_values(fields, identifier, submitted):
        field_by_key = {field.key: field for field in fields}
        unknown = sorted(set(submitted) - set(field_by_key))
        if unknown:
            raise RecordValidationError({key: "Unknown Dataset field" for key in unknown})
        if not identifier.field_keys:
            raise DatasetValidationError("Configure a Record identifier before adding records")
        for key in identifier.field_keys:
            if key not in field_by_key:
                raise DatasetValidationError("Invalid Record identifier definition")
            _require_identifier_type(field_by_key[key].data_type)
        errors: dict[str, str] = {}
        values: list[RecordValue] = []
        for field in fields:
            raw = submitted.get(field.key, "")
            try:
                canonical = canonicalize_record_value(field.data_type, raw)
            except ValidationError as error:
                errors[field.key] = str(error)
                continue
            if canonical is None:
                if field.is_required or field.key in identifier.field_keys:
                    errors[field.key] = "A value is required"
                continue
            value, key = canonical
            values.append(
                RecordValue(field.id, field.key, field.data_type, value, key, field.is_unique)
            )
        if errors:
            raise RecordValidationError(errors)
        value_by_key = {value.field_key: value for value in values}
        encoded_identifier = encode_record_identifier(
            {key: value_by_key[key].canonical_key for key in identifier.field_keys},
            expected_keys=identifier.field_keys,
        )
        return tuple(values), encoded_identifier

    def list_records(self, actor_id: str, dataset_key: str, page: int = 1) -> RecordPage:
        return _version_reads(self).read(actor_id, dataset_key, page=page, prefer_concept=True).page

    def export_dataset(
        self,
        actor_id: str,
        dataset_key: str,
        format_name: str,
        *,
        version_id=None,
        prefer_concept=False,
        variant=None,
    ):
        if not isinstance(format_name, str) or format_name not in {
            "csv",
            "csv_zip",
            "xlsx",
            "json",
        }:
            raise dataset_export.ExportGenerationError("Choose exactly one supported export format")
        if variant is not None and variant not in {"data", "flattened", "reporting"}:
            raise dataset_export.ExportGenerationError(
                "Choose Data, Flattened or Reporting for a hierarchy export"
            )
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        artifact = None
        try:
            with self.uow(translate_errors=True) as uow:
                uow.begin_export_snapshot()
                dataset = uow.datasets.find_by_key(validated_key)
                self._require_access(actor_id, dataset, "can_export_dataset", uow=uow)
                dataset_id = dataset.id
                _dataset, selected, _maintain = _version_reads(self)._selection(
                    uow, actor_id, validated_key, version_id, prefer_concept=prefer_concept
                )
                concept_identity = None
                if prefer_concept:
                    concept = uow.concepts.get(dataset_id)
                    if selected is not None or concept is None:
                        raise DatasetNotFoundError("Dataset not found")
                    concept_identity = concept.id
                if selected is None:
                    fields = tuple(uow.dataset_fields.list_for_dataset(dataset_id))
                    identifier = uow.dataset_business_keys.get(dataset_id)
                    source = uow.dataset_records.iterate_all(dataset_id)
                    export_key = (
                        f"{dataset.key}-concept"
                        if dataset.kind == "hierarchy" and variant == "reporting"
                        else dataset.key
                    )
                    hierarchy_nodes = (
                        tuple(uow.hierarchy.list_for_dataset(dataset_id))
                        if dataset.kind == "hierarchy"
                        else None
                    )
                    provenance = {"type": "concept"} if prefer_concept else {"type": "current"}
                else:
                    if selected.status not in {"Current", "Expired"}:
                        raise DatasetNotFoundError("Dataset not found")
                    fields = uow.versions.fields(selected.version)
                    identifier = DatasetBusinessKey(
                        dataset_id,
                        tuple(sorted(field.key for field in fields if field.is_identifier)),
                    )
                    source = uow.versions.iterate_records(selected.version)
                    export_key = f"{dataset.key}-v{selected.version.number}"
                    hierarchy_nodes = (
                        tuple(uow.versions.hierarchy_nodes(selected.version))
                        if dataset.kind == "hierarchy"
                        else None
                    )
                    provenance = {
                        "type": "publication",
                        "version": selected.version.number,
                        "version_id": selected.version.id,
                    }
                records = dataset_export.externally_ordered_records(
                    source,
                    lambda record: self._record_sort_key(record, identifier.field_keys),
                )
                if (
                    dataset.kind == "hierarchy"
                    and variant in {"data", "flattened"}
                    and format_name != "csv"
                ):
                    raise dataset_export.ExportGenerationError(
                        "A hierarchy variant can only be selected for CSV"
                    )
                if (
                    dataset.kind == "hierarchy"
                    and variant == "reporting"
                    and format_name not in {"csv", "xlsx"}
                ):
                    raise dataset_export.ExportGenerationError(
                        "Reporting tables are available as CSV or Excel"
                    )
                if dataset.kind != "hierarchy" and variant is not None:
                    raise dataset_export.ExportGenerationError(
                        "CSV variants are only available for hierarchy exports"
                    )
                if format_name == "csv_zip" and dataset.kind != "hierarchy":
                    raise dataset_export.ExportGenerationError(
                        "CSV bundles are only available for hierarchy exports"
                    )
                if hierarchy_nodes is None and variant is None and format_name != "csv_zip":
                    artifact = dataset_export.generate_export(
                        format_name, fields, records, export_key
                    )
                else:
                    artifact = dataset_export.generate_export(
                        format_name,
                        fields,
                        records,
                        export_key,
                        hierarchy_nodes=hierarchy_nodes,
                        hierarchy_variant=variant or "data",
                        provenance=provenance,
                    )
                dataset_export.verify_artifact_readable(artifact)
            # Authorization and content were pinned to the completed snapshot.
            # Close it before acquiring the audit writer (including SQLite).
            with self.uow(
                write=True, serialized=concept_identity is not None, translate_errors=True
            ) as uow:
                if concept_identity is not None:
                    _version_reads(self)._target(uow, actor_id, dataset_id, write=True)
                    concept = uow.concepts.get(dataset_id)
                    if concept is None or concept.id != concept_identity:
                        raise DatasetNotFoundError("Dataset not found")
                record_audit(
                    uow,
                    actor_user_id=actor_id,
                    object_type="dataset",
                    object_id=dataset_id,
                    action="dataset.export_requested",
                    details={"format": format_name},
                )
            return artifact
        except BaseException:
            if artifact is not None:
                artifact.content.close()
            raise

    @staticmethod
    def _record_sort_key(record: StoredRecord, identifier_keys: tuple[str, ...]):
        by_key = {value.field_key: value for value in record.values}
        incomplete = any(key not in by_key for key in identifier_keys)
        result = []
        for key in identifier_keys:
            value = by_key.get(key)
            if value is None:
                result.append((1, None))
                continue
            result.append(get_datatype(value.data_type).ordering_key(value.value))
        return incomplete, tuple(result), record.id

    def get_dataset_for_add(self, actor_id: str, dataset_key: str) -> DatasetRecord:
        return self._find_dataset(actor_id, dataset_key, "can_maintain_dataset")

    def _find_dataset(self, actor_id: str, key: str, permission: str) -> DatasetRecord:
        validated = DatasetFieldService._validated_dataset_lookup_key(key)
        with self.uow() as uow:
            dataset = uow.datasets.find_by_key(validated)
        self._require_access(actor_id, dataset, permission)
        return dataset

    def _require_access(
        self, actor_id: str, dataset: DatasetRecord | None, permission: str, *, uow=None
    ) -> None:
        if dataset is None or not self.authorization.has_capability(
            actor_id, permission, dataset.id, uow=uow
        ):
            raise DatasetNotFoundError("Dataset not found")
        if dataset.kind == "hierarchy" and permission != "can_export_dataset":
            raise DatasetValidationError(
                "Use the hierarchy views to maintain this table. "
                "Hierarchy import is not available yet."
            )
        if permission == "can_maintain_dataset":
            if uow is not None:
                require_concept(uow, dataset.id)
            else:
                with self.uow() as work:
                    require_concept(work, dataset.id)

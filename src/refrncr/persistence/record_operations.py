from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict

from sqlalchemy import (
    and_,
    delete,
    func,
    inspect,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer

from refrncr.datasets.datatypes import DatatypeError, get_datatype
from refrncr.persistence.common import clock_now, safe_constraint_name
from refrncr.persistence.dataset_operations import (
    SqlAlchemyDatasetBusinessKeyOperations,
    SqlAlchemyDatasetFieldOperations,
)
from refrncr.persistence.models import (
    DatasetField,
    DatasetRecord,
    DatasetRecordValue,
)
from refrncr.ports import (
    PersistenceConflictError,
    RecordValue,
    StoredRecord,
)
from refrncr.versions.version_contracts import SnapshotField


class SqlAlchemyDatasetRecordOperations:
    def __init__(self, session: Session, *, legacy_schema=False) -> None:
        self.session = session
        self._legacy_schema = legacy_schema
        self._active_records = True if legacy_schema else DatasetRecord.deleted_content.is_(None)

    def _available(self) -> bool:
        return inspect(self.session.get_bind()).has_table("dataset_records")

    def identifier_keys(self, dataset_id):
        return dict(
            self.session.execute(
                select(DatasetRecord.id, DatasetRecord.identifier_key).where(
                    DatasetRecord.dataset_id == dataset_id, self._active_records
                )
            ).all()
        )

    def create(self, *, dataset_id: str, identifier_key: bytes) -> DatasetRecord:
        record = DatasetRecord(dataset_id=dataset_id, identifier_key=identifier_key, revision=1)
        self.session.add(record)
        return record

    def get(self, dataset_id: str, record_id: str) -> StoredRecord | None:
        record = self.session.scalar(
            select(DatasetRecord).where(
                DatasetRecord.dataset_id == dataset_id,
                self._active_records,
                DatasetRecord.id == record_id,
            )
        )
        if record is None:
            return None
        return StoredRecord(
            record.id, record.dataset_id, self._values(dataset_id, record_id), record.revision
        )

    def update_if_revision(
        self,
        dataset_id: str,
        record_id: str,
        expected_revision: int,
        identifier_key: bytes,
    ) -> bool:
        try:
            result = self.session.execute(
                update(DatasetRecord)
                .where(
                    DatasetRecord.dataset_id == dataset_id,
                    self._active_records,
                    DatasetRecord.id == record_id,
                    DatasetRecord.revision == expected_revision,
                )
                .values(
                    identifier_key=identifier_key,
                    revision=DatasetRecord.revision + 1,
                    updated_at=clock_now(self.session),
                )
            )
        except IntegrityError as exc:
            raise PersistenceConflictError(safe_constraint_name(exc)) from None
        return result.rowcount == 1

    def replace_values(
        self,
        record: DatasetRecord,
        values: tuple[RecordValue, ...],
        *,
        field_ids: tuple[str, ...] | None = None,
    ) -> None:
        statement = delete(DatasetRecordValue).where(
            DatasetRecordValue.dataset_id == record.dataset_id,
            DatasetRecordValue.record_id == record.id,
        )
        if field_ids is not None:
            statement = statement.where(DatasetRecordValue.field_id.in_(field_ids))
        self.session.execute(statement)
        self.add_values(record, values)

    def delete_if_revision(self, dataset_id: str, record_id: str, expected_revision: int) -> bool:
        record = self.get(dataset_id, record_id)
        if record is None or record.revision != expected_revision:
            return False
        keys = SqlAlchemyDatasetBusinessKeyOperations(self.session).get(dataset_id).field_keys
        fields = SqlAlchemyDatasetFieldOperations(self.session).list_for_dataset(dataset_id)
        payload = {
            "fields": [
                asdict(
                    SnapshotField(
                        f.id,
                        dataset_id,
                        f.key,
                        f.name,
                        f.data_type,
                        f.is_required,
                        f.is_unique,
                        f.display_position,
                        f.key in keys,
                        f.definition,
                        f.unit,
                    )
                )
                for f in fields
            ],
            "values": {v.field_id: v.canonical_key.hex() for v in record.values},
        }
        result = self.session.execute(
            update(DatasetRecord)
            .where(
                DatasetRecord.dataset_id == dataset_id,
                DatasetRecord.id == record_id,
                DatasetRecord.revision == expected_revision,
                self._active_records,
            )
            .values(
                deleted_content=payload,
                revision=expected_revision + 1,
                updated_at=clock_now(self.session),
            )
        )
        if result.rowcount:
            self.session.execute(
                delete(DatasetRecordValue).where(
                    DatasetRecordValue.dataset_id == dataset_id,
                    DatasetRecordValue.record_id == record_id,
                )
            )
        return result.rowcount == 1

    def deleted_records(self, dataset_id):
        statement = (
            select(DatasetRecord)
            .where(
                DatasetRecord.dataset_id == dataset_id, DatasetRecord.deleted_content.is_not(None)
            )
            .order_by(DatasetRecord.id)
            .execution_options(yield_per=100)
        )
        for row in self.session.scalars(statement):
            fields = tuple(SnapshotField(**f) for f in row.deleted_content["fields"])
            values = []
            for field in fields:
                encoded = row.deleted_content["values"].get(field.id)
                if encoded is not None:
                    key = bytes.fromhex(encoded)
                    values.append(
                        RecordValue(
                            field.id,
                            field.key,
                            field.data_type,
                            get_datatype(field.data_type).from_key(key),
                            key,
                            field.is_unique,
                        )
                    )
            yield StoredRecord(row.id, dataset_id, tuple(values), row.revision), fields

    def rename_deleted_field_key(self, dataset_id: str, field_id: str, key: str) -> None:
        rows = self.session.scalars(
            select(DatasetRecord).where(
                DatasetRecord.dataset_id == dataset_id,
                DatasetRecord.deleted_content.is_not(None),
            )
        )
        for row in rows:
            payload = dict(row.deleted_content)
            fields = [dict(field) for field in payload["fields"]]
            changed = False
            for field in fields:
                if field["id"] == field_id:
                    field["key"] = key
                    changed = True
            if changed:
                payload["fields"] = fields
                row.deleted_content = payload

    def restore_if_revision(self, dataset_id, record_id, expected_revision, identifier_key):
        try:
            result = self.session.execute(
                update(DatasetRecord)
                .where(
                    DatasetRecord.dataset_id == dataset_id,
                    DatasetRecord.id == record_id,
                    DatasetRecord.revision == expected_revision,
                    DatasetRecord.deleted_content.is_not(None),
                )
                .values(
                    deleted_content=None,
                    identifier_key=identifier_key,
                    revision=expected_revision + 1,
                    updated_at=clock_now(self.session),
                )
            )
        except IntegrityError as exc:
            raise PersistenceConflictError(safe_constraint_name(exc)) from None
        return result.rowcount == 1

    def current_revision(self, dataset_id: str, record_id: str) -> int | None:
        return self.session.scalar(
            select(DatasetRecord.revision).where(
                DatasetRecord.dataset_id == dataset_id,
                self._active_records,
                DatasetRecord.id == record_id,
            )
        )

    def add_values(self, record: DatasetRecord, values: tuple[RecordValue, ...]) -> None:
        rows = []
        for value in values:
            typed = {
                "string_value": None,
                "number_value": None,
                "boolean_value": None,
                "date_value": None,
            }
            datatype = get_datatype(value.data_type)
            decoded = datatype.from_key(value.canonical_key)
            if type(value.value) is not datatype.python_type or decoded != value.value:
                raise DatatypeError("Invalid stored canonical value", "invalid_storage")
            if value.data_type in {"string", "number", "boolean", "date"}:
                typed[f"{value.data_type}_value"] = value.value
            else:
                typed["string_value"] = datatype.text(decoded)
            rows.append(
                DatasetRecordValue(
                    record_id=record.id,
                    dataset_id=record.dataset_id,
                    field_id=value.field_id,
                    canonical_value_key=value.canonical_key,
                    unique_value_key=value.canonical_key if value.is_unique else None,
                    **typed,
                )
            )
        self.session.add_all(rows)

    def add_values_by_id(
        self, dataset_id: str, record_id: str, values: tuple[RecordValue, ...]
    ) -> None:
        record = self.session.scalar(
            select(DatasetRecord).where(
                DatasetRecord.dataset_id == dataset_id,
                DatasetRecord.id == record_id,
                self._active_records,
            )
        )
        if record is None:
            raise PersistenceConflictError()
        self.add_values(record, values)

    def list_all(self, dataset_id: str) -> list[StoredRecord]:
        records = list(
            self.session.scalars(
                select(DatasetRecord)
                .where(DatasetRecord.dataset_id == dataset_id, self._active_records)
                .order_by(DatasetRecord.id)
            )
        )
        values = self.session.execute(
            select(DatasetRecordValue, DatasetField)
            .join(
                DatasetField,
                (DatasetField.id == DatasetRecordValue.field_id)
                & (DatasetField.dataset_id == DatasetRecordValue.dataset_id),
            )
            .where(DatasetRecordValue.dataset_id == dataset_id)
        )
        grouped: dict[str, list[RecordValue]] = {record.id: [] for record in records}
        for row, field in values:
            grouped[row.record_id].append(self._record_value(row, field))
        return [
            StoredRecord(record.id, record.dataset_id, tuple(grouped[record.id]), record.revision)
            for record in records
        ]

    def iterate_all(self, dataset_id: str) -> Iterator[StoredRecord]:
        statement = (
            select(DatasetRecord, DatasetRecordValue, DatasetField)
            .outerjoin(
                DatasetRecordValue,
                (DatasetRecordValue.record_id == DatasetRecord.id)
                & (DatasetRecordValue.dataset_id == DatasetRecord.dataset_id),
            )
            .outerjoin(
                DatasetField,
                (DatasetField.id == DatasetRecordValue.field_id)
                & (DatasetField.dataset_id == DatasetRecordValue.dataset_id),
            )
            .where(DatasetRecord.dataset_id == dataset_id, self._active_records)
            .order_by(DatasetRecord.id, DatasetRecordValue.field_id)
            .execution_options(yield_per=1000)
        )
        if self._legacy_schema:
            statement = statement.options(
                defer(DatasetRecord.deleted_content),
                defer(DatasetField.definition),
                defer(DatasetField.unit),
                defer(DatasetField.mapping_role),
                defer(DatasetField.source_field_id),
                defer(DatasetField.source_field_key),
                defer(DatasetField.hierarchy_scope),
            )
        current = None
        values: list[RecordValue] = []
        for record, row, field in self.session.execute(statement):
            if current is not None and record.id != current.id:
                yield StoredRecord(current.id, current.dataset_id, tuple(values), current.revision)
                values = []
            current = record
            if row is not None and field is not None:
                values.append(self._record_value(row, field))
        if current is not None:
            yield StoredRecord(current.id, current.dataset_id, tuple(values), current.revision)

    @staticmethod
    def _record_value(row: DatasetRecordValue, field: DatasetField) -> RecordValue:
        datatype = get_datatype(field.data_type)
        value = datatype.from_key(row.canonical_value_key)
        columns = {
            "string": row.string_value,
            "number": row.number_value,
            "boolean": row.boolean_value,
            "date": row.date_value,
        }
        column = field.data_type if field.data_type in columns else "string"
        if sum(item is not None for item in columns.values()) != 1 or columns[column] is None:
            raise DatatypeError("Invalid stored canonical value", "invalid_storage")
        # Numeric is an approximate SQLite projection; the exact key is authoritative.
        expected = value if field.data_type in columns else datatype.text(value)
        if field.data_type != "number" and columns[column] != expected:
            raise DatatypeError("Invalid stored canonical value", "invalid_storage")
        return RecordValue(
            field.id,
            field.key,
            field.data_type,
            value,
            row.canonical_value_key,
            field.is_unique,
        )

    def existing_identifier_keys(
        self, dataset_id: str, identifier_keys: tuple[bytes, ...]
    ) -> set[bytes]:
        found: set[bytes] = set()
        for start in range(0, len(identifier_keys), 500):
            chunk = identifier_keys[start : start + 500]
            found.update(
                self.session.scalars(
                    select(DatasetRecord.identifier_key).where(
                        DatasetRecord.dataset_id == dataset_id,
                        self._active_records,
                        DatasetRecord.identifier_key.in_(chunk),
                    )
                )
            )
        return found

    def existing_unique_value_keys(
        self, dataset_id: str, values: tuple[tuple[str, bytes], ...]
    ) -> set[tuple[str, bytes]]:
        found: set[tuple[str, bytes]] = set()
        for start in range(0, len(values), 250):
            chunk = values[start : start + 250]
            if not chunk:
                continue
            rows = self.session.execute(
                select(
                    DatasetRecordValue.field_id,
                    DatasetRecordValue.unique_value_key,
                ).where(
                    DatasetRecordValue.dataset_id == dataset_id,
                    or_(
                        *(
                            and_(
                                DatasetRecordValue.field_id == field_id,
                                DatasetRecordValue.unique_value_key == key,
                            )
                            for field_id, key in chunk
                        )
                    ),
                )
            )
            found.update((field_id, key) for field_id, key in rows if key is not None)
        return found

    def _values(self, dataset_id: str, record_id: str) -> tuple[RecordValue, ...]:
        rows = self.session.execute(
            select(DatasetRecordValue, DatasetField)
            .join(
                DatasetField,
                (DatasetField.id == DatasetRecordValue.field_id)
                & (DatasetField.dataset_id == DatasetRecordValue.dataset_id),
            )
            .where(
                DatasetRecordValue.dataset_id == dataset_id,
                DatasetRecordValue.record_id == record_id,
            )
        )
        return tuple(self._record_value(row, field) for row, field in rows)

    def count(self, dataset_id: str) -> int:
        if not self._available():
            return 0
        return (
            self.session.scalar(
                select(func.count(DatasetRecord.id)).where(
                    DatasetRecord.dataset_id == dataset_id, self._active_records
                )
            )
            or 0
        )

    def field_has_values(self, dataset_id: str, field_id: str) -> bool:
        if not self._available():
            return False
        return (
            self.session.scalar(
                select(DatasetRecordValue.record_id)
                .where(
                    DatasetRecordValue.dataset_id == dataset_id,
                    DatasetRecordValue.field_id == field_id,
                )
                .limit(1)
            )
            is not None
        )

    def field_has_missing_values(self, dataset_id: str, field_id: str) -> bool:
        if not self._available():
            return False
        value_records = select(DatasetRecordValue.record_id).where(
            DatasetRecordValue.dataset_id == dataset_id,
            DatasetRecordValue.field_id == field_id,
        )
        return (
            self.session.scalar(
                select(DatasetRecord.id)
                .where(
                    DatasetRecord.dataset_id == dataset_id,
                    self._active_records,
                    DatasetRecord.id.not_in(value_records),
                )
                .limit(1)
            )
            is not None
        )

    def field_has_duplicate_values(self, dataset_id: str, field_id: str) -> bool:
        if not self._available():
            return False
        return (
            self.session.scalar(
                select(DatasetRecordValue.canonical_value_key)
                .where(
                    DatasetRecordValue.dataset_id == dataset_id,
                    DatasetRecordValue.field_id == field_id,
                )
                .group_by(DatasetRecordValue.canonical_value_key)
                .having(func.count() > 1)
                .limit(1)
            )
            is not None
        )

    def set_field_unique_keys(self, dataset_id: str, field_id: str, enabled: bool) -> None:
        if not self._available():
            return
        self.session.execute(
            update(DatasetRecordValue)
            .where(
                DatasetRecordValue.dataset_id == dataset_id,
                DatasetRecordValue.field_id == field_id,
            )
            .values(unique_value_key=(DatasetRecordValue.canonical_value_key if enabled else None))
        )

"""Append-only snapshot adapter. Callers own the locked Dataset transaction."""

from dataclasses import asdict

from sqlalchemy import func, select, update

from refrncr.datatypes import DatatypeError, get_datatype
from refrncr.models import (
    DatasetVersion,
    DatasetVersionField,
    DatasetVersionRecord,
    DatasetVersionValue,
    new_id,
)
from refrncr.ports import PersistenceConflictError, RecordValue, StoredRecord
from refrncr.version_contracts import (
    PublishedVersion,
    SnapshotContent,
    SnapshotField,
    SummaryCounts,
)


class SqlAlchemyVersionOperations:
    def __init__(self, session):
        self.session = session

    @staticmethod
    def _version(row):
        return PublishedVersion(
            row.id,
            row.dataset_id,
            row.number,
            row.published_at,
            row.publisher_id,
            row.effective_date,
            row.release_note,
            row.name,
            row.description,
            row.field_count,
            row.record_count,
            SummaryCounts(*(getattr(row, key) for key in SummaryCounts.__dataclass_fields__)),
            row.cancelled_at,
            row.cancelling_actor_id,
        )

    def list_for_dataset(self, dataset_id):
        return tuple(
            self._version(row)
            for row in self.session.scalars(
                select(DatasetVersion)
                .where(DatasetVersion.dataset_id == dataset_id)
                .order_by(DatasetVersion.number.desc())
            )
        )

    def current_for_datasets(self, dataset_ids, today):
        if dataset_ids == ():
            return {}
        ranked = select(
            DatasetVersion.id,
            func.row_number()
            .over(
                partition_by=DatasetVersion.dataset_id,
                order_by=(DatasetVersion.effective_date.desc(), DatasetVersion.number.desc()),
            )
            .label("position"),
        ).where(DatasetVersion.cancelled_at.is_(None), DatasetVersion.effective_date <= today)
        if dataset_ids is not None:
            ranked = ranked.where(DatasetVersion.dataset_id.in_(dataset_ids))
        ranked = ranked.subquery()
        rows = self.session.scalars(
            select(DatasetVersion)
            .join(ranked, ranked.c.id == DatasetVersion.id)
            .where(ranked.c.position == 1)
        )
        return {row.dataset_id: self._version(row) for row in rows}

    def append(
        self,
        dataset_id,
        number,
        published_at,
        publisher_id,
        effective_date,
        release_note,
        content,
        summary,
    ):
        row = DatasetVersion(
            id=new_id(),
            dataset_id=dataset_id,
            number=number,
            published_at=published_at,
            publisher_id=publisher_id,
            effective_date=effective_date,
            release_note=release_note,
            name=content.name,
            description=content.description,
            field_count=len(content.fields),
            record_count=len(content.records),
            **asdict(summary),
        )
        self.session.add(row)
        self.session.flush()
        for field in content.fields:
            values = asdict(field)
            values.pop("dataset_id")
            self.session.add(DatasetVersionField(version_id=row.id, **values))
        self.session.flush()
        for record in content.records:
            by_key = {v.field_key: v.canonical_key for v in record.values}
            encoded = b"".join(
                len(by_key[k]).to_bytes(4, "big") + by_key[k] for k in content.identifier_keys
            )
            self.session.add(
                DatasetVersionRecord(version_id=row.id, id=record.id, identifier_key=encoded)
            )
        self.session.flush()
        for record in content.records:
            for value in record.values:
                datatype = get_datatype(value.data_type)
                decoded = datatype.from_key(value.canonical_key)
                self.session.add(
                    DatasetVersionValue(
                        version_id=row.id,
                        record_id=record.id,
                        field_id=value.field_id,
                        canonical_key=value.canonical_key,
                        canonical_text=datatype.text(decoded),
                        unique_key=value.canonical_key if value.is_unique else None,
                    )
                )
        self.session.flush()
        return self._version(row)

    def cancel(self, dataset_id, version_id, actor_id, timestamp, today):
        result = self.session.execute(
            update(DatasetVersion)
            .where(
                DatasetVersion.id == version_id,
                DatasetVersion.dataset_id == dataset_id,
                DatasetVersion.cancelled_at.is_(None),
                DatasetVersion.effective_date > today,
            )
            .values(cancelled_at=timestamp, cancelling_actor_id=actor_id)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise PersistenceConflictError()

    def fields(self, version):
        return tuple(
            SnapshotField(
                f.id,
                version.dataset_id,
                f.key,
                f.name,
                f.data_type,
                f.is_required,
                f.is_unique,
                f.display_position,
                f.is_identifier,
            )
            for f in self.session.scalars(
                select(DatasetVersionField)
                .where(DatasetVersionField.version_id == version.id)
                .order_by(DatasetVersionField.display_position)
            )
        )

    def iterate_records(self, version):
        statement = (
            select(DatasetVersionRecord, DatasetVersionValue, DatasetVersionField)
            .outerjoin(
                DatasetVersionValue,
                (DatasetVersionValue.version_id == DatasetVersionRecord.version_id)
                & (DatasetVersionValue.record_id == DatasetVersionRecord.id),
            )
            .outerjoin(
                DatasetVersionField,
                (DatasetVersionField.version_id == DatasetVersionValue.version_id)
                & (DatasetVersionField.id == DatasetVersionValue.field_id),
            )
            .where(DatasetVersionRecord.version_id == version.id)
            .order_by(DatasetVersionRecord.id, DatasetVersionValue.field_id)
            .execution_options(yield_per=1000)
        )
        current, values = None, []
        for record, value, field in self.session.execute(statement):
            if current is not None and current != record.id:
                yield StoredRecord(current, version.dataset_id, tuple(values), 1)
                values = []
            current = record.id
            if value is not None:
                datatype = get_datatype(field.data_type)
                decoded = datatype.from_key(value.canonical_key)
                if datatype.text(decoded) != value.canonical_text:
                    raise DatatypeError("Invalid stored canonical value", "invalid_storage")
                values.append(
                    RecordValue(
                        field.id,
                        field.key,
                        field.data_type,
                        decoded,
                        value.canonical_key,
                        field.is_unique,
                    )
                )
        if current is not None:
            yield StoredRecord(current, version.dataset_id, tuple(values), 1)

    def content(self, version):
        return SnapshotContent(
            version.name,
            version.description,
            self.fields(version),
            tuple(self.iterate_records(version)),
        )

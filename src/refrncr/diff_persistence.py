"""Relational diff projections; SQL joins canonical keys, never ORM object graphs."""

from sqlalchemy import and_, case, func, literal, or_, select, union_all

from refrncr.datatypes import get_datatype
from refrncr.diff_contracts import PAGE_SIZE, RecordProjection, ValueChange
from refrncr.models import (
    DatasetField,
    DatasetRecord,
    DatasetRecordValue,
    DatasetVersionField,
    DatasetVersionRecord,
    DatasetVersionValue,
)


class SqlAlchemyDiffOperations:
    def __init__(self, session):
        self.session = session

    @staticmethod
    def _changes(dataset_id, version_id):
        new = (
            select(
                DatasetRecordValue.record_id.label("record_id"),
                DatasetRecordValue.field_id.label("field_id"),
                DatasetRecordValue.canonical_value_key.label("value"),
            )
            .where(DatasetRecordValue.dataset_id == dataset_id)
            .subquery()
        )
        old = (
            select(
                DatasetVersionValue.record_id.label("record_id"),
                DatasetVersionValue.field_id.label("field_id"),
                DatasetVersionValue.canonical_key.label("value"),
            )
            .where(DatasetVersionValue.version_id == version_id)
            .subquery()
        )
        common = (
            select(DatasetField.id)
            .join(
                DatasetVersionField,
                and_(
                    DatasetVersionField.id == DatasetField.id,
                    DatasetVersionField.version_id == version_id,
                ),
            )
            .where(
                DatasetField.dataset_id == dataset_id,
                DatasetField.data_type == DatasetVersionField.data_type,
            )
        )
        changed = (
            select(new.c.record_id)
            .select_from(
                new.outerjoin(
                    old, and_(old.c.record_id == new.c.record_id, old.c.field_id == new.c.field_id)
                )
            )
            .where(
                new.c.field_id.in_(common), or_(old.c.value.is_(None), old.c.value != new.c.value)
            )
        )
        removed = (
            select(old.c.record_id)
            .select_from(
                old.outerjoin(
                    new, and_(old.c.record_id == new.c.record_id, old.c.field_id == new.c.field_id)
                )
            )
            .where(old.c.field_id.in_(common), new.c.value.is_(None))
        )
        values = union_all(changed, removed).subquery()
        return (
            select(values.c.record_id, func.count().label("count"))
            .group_by(values.c.record_id)
            .subquery()
        )

    @classmethod
    def _records(cls, dataset_id, version_id):
        new = (
            select(DatasetRecord.id, DatasetRecord.identifier_key)
            .where(DatasetRecord.dataset_id == dataset_id)
            .subquery()
        )
        old = (
            select(DatasetVersionRecord.id, DatasetVersionRecord.identifier_key)
            .where(DatasetVersionRecord.version_id == version_id)
            .subquery()
        )
        changes = cls._changes(dataset_id, version_id)
        current = (
            select(
                new.c.id,
                case((old.c.id.is_(None), "Added"), else_="Changed").label("status"),
                old.c.identifier_key.label("before_identifier"),
                new.c.identifier_key.label("after_identifier"),
                func.coalesce(changes.c.count, 0).label("changed_values"),
            )
            .select_from(
                new.outerjoin(old, old.c.id == new.c.id).outerjoin(
                    changes, changes.c.record_id == new.c.id
                )
            )
            .where(or_(old.c.id.is_(None), changes.c.count > 0))
        )
        removed = (
            select(old.c.id, literal("Removed"), old.c.identifier_key, literal(None), literal(0))
            .select_from(old.outerjoin(new, new.c.id == old.c.id))
            .where(new.c.id.is_(None))
        )
        return union_all(current, removed).subquery()

    def record_states(self, dataset_id, version_id, record_ids):
        if not record_ids:
            return {}
        rows = self._records(dataset_id, version_id)
        return dict(
            self.session.execute(
                select(rows.c.id, rows.c.status).where(rows.c.id.in_(record_ids))
            ).all()
        )

    def removed_records(self, dataset_id, version):
        # Selection is the same REF-83/84 projection used for summary and review.
        from refrncr.version_persistence import SqlAlchemyVersionOperations

        rows = self._records(dataset_id, version.id)
        ids = select(rows.c.id).where(rows.c.status == "Removed")
        yield from SqlAlchemyVersionOperations(self.session).iterate_records(
            version, record_ids=ids
        )

    def counts(self, dataset_id, version_id):
        rows = self._records(dataset_id, version_id)
        counts = dict(
            self.session.execute(select(rows.c.status, func.count()).group_by(rows.c.status)).all()
        )
        return tuple(counts.get(status, 0) for status in ("Added", "Changed", "Removed"))

    def records(self, dataset_id, version_id, statuses, record_id=None):
        rows = self._records(dataset_id, version_id)
        statement = select(rows).where(rows.c.status.in_(statuses))
        if record_id is not None:
            statement = statement.where(rows.c.id == record_id)
        # Scalar identifiers and counts only. Memory never depends on Record width.
        for row in self.session.execute(statement.execution_options(yield_per=100)):
            yield RecordProjection(*row)

    def validate_snapshot(self, version):
        """Validate every scalar snapshot value and identifier with bounded memory."""
        identifier_fields = tuple(
            self.session.scalars(
                select(DatasetVersionField.key)
                .where(
                    DatasetVersionField.version_id == version.id,
                    DatasetVersionField.is_identifier.is_(True),
                )
                .order_by(DatasetVersionField.key)
            )
        )
        statement = (
            select(
                DatasetVersionRecord.id,
                DatasetVersionRecord.identifier_key,
                DatasetVersionValue.field_id,
                DatasetVersionValue.canonical_key,
                DatasetVersionValue.canonical_text,
                DatasetVersionField.key,
                DatasetVersionField.data_type,
                DatasetVersionField.is_identifier,
            )
            .select_from(DatasetVersionRecord)
            .outerjoin(
                DatasetVersionValue,
                and_(
                    DatasetVersionValue.version_id == DatasetVersionRecord.version_id,
                    DatasetVersionValue.record_id == DatasetVersionRecord.id,
                ),
            )
            .outerjoin(
                DatasetVersionField,
                and_(
                    DatasetVersionField.version_id == DatasetVersionValue.version_id,
                    DatasetVersionField.id == DatasetVersionValue.field_id,
                ),
            )
            .where(DatasetVersionRecord.version_id == version.id)
            .order_by(DatasetVersionRecord.id, DatasetVersionField.key)
        )
        return self._validate_rows(statement, identifier_fields, version.record_count)

    def validate_concept(self, dataset_id, fields):
        identifier_fields = tuple(sorted(f.key for f in fields if f.is_identifier))
        statement = (
            select(
                DatasetRecord.id,
                DatasetRecord.identifier_key,
                DatasetRecordValue.field_id,
                DatasetRecordValue.canonical_value_key,
                literal(None),
                DatasetField.key,
                DatasetField.data_type,
                DatasetField.key.in_(identifier_fields),
            )
            .select_from(DatasetRecord)
            .outerjoin(
                DatasetRecordValue,
                and_(
                    DatasetRecordValue.dataset_id == DatasetRecord.dataset_id,
                    DatasetRecordValue.record_id == DatasetRecord.id,
                ),
            )
            .outerjoin(
                DatasetField,
                and_(
                    DatasetField.dataset_id == DatasetRecordValue.dataset_id,
                    DatasetField.id == DatasetRecordValue.field_id,
                ),
            )
            .where(DatasetRecord.dataset_id == dataset_id)
            .order_by(DatasetRecord.id, DatasetField.key)
        )
        return self._validate_rows(statement, identifier_fields)

    def _validate_rows(self, statement, identifier_fields, expected_count=None):
        from uuid import UUID

        from refrncr.datatypes import DatatypeError

        record_id, encoded, components, count = None, None, [], 0

        def valid_identifier():
            return tuple(
                key for key, _value in components
            ) == identifier_fields and encoded == b"".join(
                len(value).to_bytes(4, "big") + value for _key, value in components
            )

        for (
            identity,
            identifier,
            field_id,
            key,
            text,
            field_key,
            data_type,
            is_identifier,
        ) in self.session.execute(statement.execution_options(yield_per=100)):
            if identity != record_id:
                if record_id is not None and not valid_identifier():
                    return False
                try:
                    if str(UUID(identity)) != identity:
                        return False
                except (ValueError, TypeError):
                    return False
                record_id, encoded, components = identity, identifier, []
                count += 1
            if field_id is not None:
                datatype = get_datatype(data_type)
                decoded = datatype.from_key(key)
                if text is not None and datatype.text(decoded) != text:
                    raise DatatypeError("Invalid stored canonical value", "invalid_storage")
                if is_identifier:
                    components.append((field_key, key))
                    if sum(len(value) + 4 for _field, value in components) > 2000:
                        return False
        return (expected_count is None or count == expected_count) and (
            record_id is None or valid_identifier()
        )

    def values(self, dataset_id, version_id, record_id, status, page):
        new = (
            select(
                DatasetField.id,
                DatasetField.name,
                DatasetField.data_type,
                DatasetField.display_position,
            )
            .where(DatasetField.dataset_id == dataset_id)
            .subquery()
        )
        old = (
            select(
                DatasetVersionField.id,
                DatasetVersionField.name,
                DatasetVersionField.data_type,
                DatasetVersionField.display_position,
            )
            .where(DatasetVersionField.version_id == version_id)
            .subquery()
        )
        nv = (
            select(
                DatasetRecordValue.field_id, DatasetRecordValue.canonical_value_key.label("value")
            )
            .where(
                DatasetRecordValue.dataset_id == dataset_id,
                DatasetRecordValue.record_id == record_id,
            )
            .subquery()
        )
        ov = (
            select(DatasetVersionValue.field_id, DatasetVersionValue.canonical_key.label("value"))
            .where(
                DatasetVersionValue.version_id == version_id,
                DatasetVersionValue.record_id == record_id,
            )
            .subquery()
        )
        if status == "Removed":
            query = select(
                old.c.id,
                old.c.name,
                old.c.data_type.label("before_type"),
                literal(None).label("after_type"),
                ov.c.value.label("before"),
                literal(None).label("after"),
                old.c.display_position,
            ).select_from(old.outerjoin(ov, ov.c.field_id == old.c.id))
        else:
            query = select(
                new.c.id,
                new.c.name,
                old.c.data_type.label("before_type")
                if status == "Changed"
                else literal(None).label("before_type"),
                new.c.data_type.label("after_type"),
                ov.c.value.label("before")
                if status == "Changed"
                else literal(None).label("before"),
                nv.c.value.label("after"),
                new.c.display_position,
            ).select_from(
                new.outerjoin(old, old.c.id == new.c.id)
                .outerjoin(nv, nv.c.field_id == new.c.id)
                .outerjoin(ov, ov.c.field_id == new.c.id)
            )
            if status == "Changed":
                query = query.where(
                    old.c.id.is_not(None),
                    new.c.data_type == old.c.data_type,
                    or_(
                        nv.c.value != ov.c.value,
                        and_(nv.c.value.is_(None), ov.c.value.is_not(None)),
                        and_(ov.c.value.is_(None), nv.c.value.is_not(None)),
                    ),
                )
        rows = query.subquery()
        total = self.session.scalar(select(func.count()).select_from(rows))
        page_rows = self.session.execute(
            select(rows)
            .order_by(rows.c.display_position, rows.c.id)
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )

        def rendered(data_type, key):
            if key is None:
                return None
            datatype = get_datatype(data_type)
            return datatype.text(datatype.from_key(key))

        return tuple(
            ValueChange(
                row.id,
                row.name,
                row.before_type,
                row.after_type,
                rendered(row.before_type, row.before),
                rendered(row.after_type, row.after),
            )
            for row in page_rows
        ), total

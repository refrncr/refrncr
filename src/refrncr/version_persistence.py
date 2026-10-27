"""Append-only snapshot adapter. Callers own the locked Dataset transaction."""

from collections import defaultdict
from dataclasses import asdict

from sqlalchemy import Numeric, cast, func, select, update
from sqlalchemy.orm import aliased, undefer

from refrncr.datatypes import DatatypeError, get_datatype
from refrncr.hierarchy_contracts import HierarchyNode
from refrncr.hierarchy_persistence import hierarchy_value
from refrncr.models import (
    Dataset,
    DatasetVersion,
    DatasetVersionField,
    DatasetVersionHierarchyNode,
    DatasetVersionHierarchyValue,
    DatasetVersionRecord,
    DatasetVersionValue,
    new_id,
)
from refrncr.ports import PersistenceConflictError, RecordValue, StoredRecord
from refrncr.record_identity import encode_record_identifier
from refrncr.version_contracts import (
    PublishedVersion,
    SnapshotContent,
    SnapshotField,
    SourceVersion,
    SummaryCounts,
)


class SqlAlchemyVersionOperations:
    def __init__(self, session, *, legacy_metadata=False):
        self.session = session
        self.legacy_metadata = legacy_metadata

    def _select_version(self):
        statement = select(DatasetVersion)
        return (
            statement
            if self.legacy_metadata
            else statement.options(undefer(DatasetVersion.source_version_id))
        )

    def _version(self, row):
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
            None if self.legacy_metadata else row.source_version_id,
        )

    def list_for_dataset(self, dataset_id):
        return tuple(
            self._version(row)
            for row in self.session.scalars(
                self._select_version()
                .where(DatasetVersion.dataset_id == dataset_id)
                .order_by(DatasetVersion.number.desc())
            )
        )

    def get(self, dataset_id, version_id):
        row = self.session.scalar(
            self._select_version().where(
                DatasetVersion.dataset_id == dataset_id, DatasetVersion.id == version_id
            )
        )
        return self._version(row) if row else None

    def list_for_datasets(self, dataset_ids):
        """Batch publication metadata only, without snapshot fields or records."""
        result = defaultdict(list)
        if dataset_ids:
            for row in self.session.scalars(
                self._select_version()
                .where(DatasetVersion.dataset_id.in_(dataset_ids))
                .order_by(DatasetVersion.number.desc())
            ):
                result[row.dataset_id].append(self._version(row))
        return dict(result)

    def source_references(self, source_version_ids):
        """Only public source keys/numbers, batched within the caller's read snapshot."""
        if not source_version_ids:
            return {}
        rows = self.session.execute(
            select(DatasetVersion.id, Dataset.key, DatasetVersion.number)
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .where(DatasetVersion.id.in_(source_version_ids))
        )
        return {version_id: SourceVersion(key, number) for version_id, key, number in rows}

    def source_updates(self, source_version_ids):
        if not source_version_ids:
            return frozenset()
        newer = aliased(DatasetVersion)
        return frozenset(
            self.session.scalars(
                select(DatasetVersion.id).where(
                    DatasetVersion.id.in_(source_version_ids),
                    select(newer.id)
                    .where(
                        newer.dataset_id == DatasetVersion.dataset_id,
                        newer.number > DatasetVersion.number,
                        newer.cancelled_at.is_(None),
                    )
                    .exists(),
                )
            )
        )

    def latest_non_cancelled(self, dataset_id):
        row = self.session.scalar(
            self._select_version()
            .where(DatasetVersion.dataset_id == dataset_id, DatasetVersion.cancelled_at.is_(None))
            .order_by(DatasetVersion.number.desc())
            .limit(1)
        )
        return self._version(row) if row else None

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
            self._select_version()
            .join(ranked, ranked.c.id == DatasetVersion.id)
            .where(ranked.c.position == 1)
        )
        return {row.dataset_id: self._version(row) for row in rows}

    def current_page(self, today, *, dataset_ids=None, offset, limit, include_hierarchy=False):
        """Database page of Current publications; no concepts or historical fallback."""
        ranked = (
            select(
                DatasetVersion.id,
                func.row_number()
                .over(
                    partition_by=DatasetVersion.dataset_id,
                    order_by=(DatasetVersion.effective_date.desc(), DatasetVersion.number.desc()),
                )
                .label("position"),
            )
            .where(DatasetVersion.cancelled_at.is_(None), DatasetVersion.effective_date <= today)
            .subquery()
        )
        statement = (
            select(Dataset, DatasetVersion)
            .options(undefer(DatasetVersion.source_version_id))
            .join(DatasetVersion, DatasetVersion.dataset_id == Dataset.id)
            .join(ranked, ranked.c.id == DatasetVersion.id)
            .where(ranked.c.position == 1)
        )
        if not include_hierarchy:
            statement = statement.where(Dataset.kind != "hierarchy")
        if dataset_ids is not None:
            statement = statement.where(Dataset.id.in_(dataset_ids))
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.session.execute(statement.order_by(Dataset.key).offset(offset).limit(limit))
        return tuple((dataset, self._version(version)) for dataset, version in rows), total

    def selected_for_datasets(self, dataset_ids, effective_date, version_number=None):
        """Select one published version per Dataset for a business date or number."""
        if dataset_ids == ():
            return {}
        ranked = select(
            DatasetVersion.id,
            func.row_number()
            .over(
                partition_by=DatasetVersion.dataset_id,
                order_by=DatasetVersion.effective_date.desc(),
            )
            .label("position"),
        ).where(
            DatasetVersion.cancelled_at.is_(None),
            DatasetVersion.effective_date <= effective_date,
        )
        if dataset_ids is not None:
            ranked = ranked.where(DatasetVersion.dataset_id.in_(dataset_ids))
        ranked = ranked.subquery()
        statement = (
            self._select_version()
            .join(ranked, ranked.c.id == DatasetVersion.id)
            .where(ranked.c.position == 1)
        )
        if version_number is not None:
            statement = statement.where(DatasetVersion.number == version_number)
        rows = self.session.scalars(statement)
        return {row.dataset_id: self._version(row) for row in rows}

    def selected_page(
        self,
        effective_date,
        *,
        version_number=None,
        dataset_ids=None,
        offset,
        limit,
        include_hierarchy=False,
    ):
        ranked = select(
            DatasetVersion.id,
            func.row_number()
            .over(
                partition_by=DatasetVersion.dataset_id,
                order_by=DatasetVersion.effective_date.desc(),
            )
            .label("position"),
        ).where(
            DatasetVersion.cancelled_at.is_(None),
            DatasetVersion.effective_date <= effective_date,
        )
        if dataset_ids is not None:
            ranked = ranked.where(DatasetVersion.dataset_id.in_(dataset_ids))
        ranked = ranked.subquery()
        statement = (
            select(Dataset, DatasetVersion)
            .options(undefer(DatasetVersion.source_version_id))
            .join(DatasetVersion, DatasetVersion.dataset_id == Dataset.id)
            .join(ranked, ranked.c.id == DatasetVersion.id)
            .where(ranked.c.position == 1)
        )
        if not include_hierarchy:
            statement = statement.where(Dataset.kind != "hierarchy")
        if version_number is not None:
            statement = statement.where(DatasetVersion.number == version_number)
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.session.execute(statement.order_by(Dataset.key).offset(offset).limit(limit))
        return tuple((dataset, self._version(version)) for dataset, version in rows), total

    def version_for_datasets(self, dataset_ids, version_number):
        if dataset_ids == ():
            return {}
        statement = self._select_version().where(
            DatasetVersion.cancelled_at.is_(None), DatasetVersion.number == version_number
        )
        if dataset_ids is not None:
            statement = statement.where(DatasetVersion.dataset_id.in_(dataset_ids))
        return {row.dataset_id: self._version(row) for row in self.session.scalars(statement)}

    def version_page(
        self, version_number, *, dataset_ids=None, offset, limit, include_hierarchy=False
    ):
        statement = (
            select(Dataset, DatasetVersion)
            .options(undefer(DatasetVersion.source_version_id))
            .join(DatasetVersion, DatasetVersion.dataset_id == Dataset.id)
            .where(
                DatasetVersion.cancelled_at.is_(None),
                DatasetVersion.number == version_number,
            )
        )
        if not include_hierarchy:
            statement = statement.where(Dataset.kind != "hierarchy")
        if dataset_ids is not None:
            statement = statement.where(Dataset.id.in_(dataset_ids))
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.session.execute(statement.order_by(Dataset.key).offset(offset).limit(limit))
        return tuple((dataset, self._version(version)) for dataset, version in rows), total

    def record_identity(self, version, identifier_key):
        return self.session.scalar(
            select(DatasetVersionRecord.id).where(
                DatasetVersionRecord.version_id == version.id,
                DatasetVersionRecord.identifier_key == identifier_key,
            )
        )

    def record_page(self, version, fields, *, offset, limit):
        """Sort canonical identifier components in SQL, then load only page values."""
        order = []
        sqlite = self.session.bind.dialect.name == "sqlite"
        for field in sorted((f for f in fields if f.is_identifier), key=lambda f: f.key):
            value = (
                select(DatasetVersionValue.canonical_text)
                .where(
                    DatasetVersionValue.version_id == version.id,
                    DatasetVersionValue.record_id == DatasetVersionRecord.id,
                    DatasetVersionValue.field_id == field.id,
                )
                .scalar_subquery()
            )
            if field.data_type in {"integer", "number"}:
                value = value.collate("refrncr_number") if sqlite else cast(value, Numeric())
            else:
                # UTC Z is a suffix, not part of temporal ordering (fraction vs whole second).
                if field.data_type == "timestamp":
                    value = func.rtrim(value, "Z")
                value = value.collate("BINARY" if sqlite else "C")
            order.append(value.asc().nulls_last())
        ids = tuple(
            self.session.scalars(
                select(DatasetVersionRecord.id)
                .where(DatasetVersionRecord.version_id == version.id)
                .order_by(*order, DatasetVersionRecord.id)
                .offset(offset)
                .limit(limit)
            )
        )
        if not ids:
            return ()
        records = {r.id: r for r in self.iterate_records(version, record_ids=ids)}
        return tuple(records[key] for key in ids)

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
            source_version_id=content.source_version_id,
            field_count=len(content.fields),
            record_count=len(content.records),
            **asdict(summary),
        )
        if self.legacy_metadata:
            from sqlalchemy import MetaData, Table

            table = Table("dataset_versions", MetaData(), autoload_with=self.session.connection())
            self.session.execute(
                table.insert().values(
                    **{column.name: getattr(row, column.name) for column in table.columns}
                )
            )
        else:
            self.session.add(row)
        self.session.flush()
        if self.legacy_metadata:
            from sqlalchemy import MetaData, Table

            legacy_fields = Table(
                "dataset_version_fields", MetaData(), autoload_with=self.session.connection()
            )
        for field in content.fields:
            values = asdict(field)
            values.pop("dataset_id")
            if self.legacy_metadata:
                values.pop("definition")
                values.pop("unit")
                values.pop("mapping_role")
                values.pop("source_field_key")
                values.pop("hierarchy_scope")
                self.session.execute(legacy_fields.insert().values(version_id=row.id, **values))
            else:
                self.session.add(DatasetVersionField(version_id=row.id, **values))
        self.session.flush()
        for record in content.records:
            by_key = {v.field_key: v.canonical_key for v in record.values}
            encoded = (
                encode_record_identifier(
                    {key: by_key.get(key) for key in content.identifier_keys},
                    expected_keys=content.identifier_keys,
                )
                if content.identifier_keys
                else b""
            )
            if content.hierarchy_nodes and not content.identifier_keys:
                encoded = b"hierarchy:" + record.id.encode("ascii")
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
        for node in content.hierarchy_nodes:
            self.session.add(
                DatasetVersionHierarchyNode(
                    version_id=row.id, id=node.id, name=node.name, parent_id=node.parent_id
                )
            )
        self.session.flush()
        for node in content.hierarchy_nodes:
            for value in node.values:
                datatype = get_datatype(value.data_type)
                self.session.add(
                    DatasetVersionHierarchyValue(
                        version_id=row.id,
                        node_id=node.id,
                        field_id=value.field_id,
                        canonical_key=value.canonical_key,
                        canonical_text=datatype.text(datatype.from_key(value.canonical_key)),
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
        if self.legacy_metadata:
            from sqlalchemy import MetaData, Table

            table = Table(
                "dataset_version_fields", MetaData(), autoload_with=self.session.connection()
            )
            return tuple(
                SnapshotField(
                    dataset_id=version.dataset_id,
                    **{key: value for key, value in row.items() if key != "version_id"},
                )
                for row in self.session.execute(
                    select(table)
                    .where(table.c.version_id == version.id)
                    .order_by(table.c.display_position)
                ).mappings()
            )
        return self.fields_for_versions((version,)).get(version.id, ())

    def fields_for_versions(self, versions):
        if not versions:
            return {}
        version_ids = tuple(version.id for version in versions)
        dataset_ids = {version.id: version.dataset_id for version in versions}
        if self.legacy_metadata:
            from sqlalchemy import MetaData, Table

            table = Table(
                "dataset_version_fields", MetaData(), autoload_with=self.session.connection()
            )
            rows = list(
                self.session.execute(
                    select(table)
                    .where(table.c.version_id.in_(version_ids))
                    .order_by(table.c.version_id, table.c.display_position)
                ).mappings()
            )
            return {
                version_id: tuple(
                    SnapshotField(
                        dataset_id=dataset_ids[version_id],
                        **{key: value for key, value in row.items() if key != "version_id"},
                    )
                    for row in rows
                    if row["version_id"] == version_id
                )
                for version_id in version_ids
            }
        rows = self.session.scalars(
            select(DatasetVersionField)
            .where(DatasetVersionField.version_id.in_(version_ids))
            .order_by(DatasetVersionField.version_id, DatasetVersionField.display_position)
        )
        result = {version_id: [] for version_id in version_ids}
        for field in rows:
            result[field.version_id].append(
                SnapshotField(
                    field.id,
                    dataset_ids[field.version_id],
                    field.key,
                    field.name,
                    field.data_type,
                    field.is_required,
                    field.is_unique,
                    field.display_position,
                    field.is_identifier,
                    field.definition,
                    field.unit,
                    field.mapping_role,
                    field.source_field_key,
                    field.hierarchy_scope,
                )
            )
        return {version_id: tuple(fields) for version_id, fields in result.items()}

    def iterate_records(self, version, *, record_ids=None):
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
        if record_ids is not None:
            statement = statement.where(DatasetVersionRecord.id.in_(record_ids))
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

    def record_ids(self, version):
        return tuple(
            self.session.scalars(
                select(DatasetVersionRecord.id).where(DatasetVersionRecord.version_id == version.id)
            )
        )

    def content(self, version):
        return SnapshotContent(
            version.name,
            version.description,
            self.fields(version),
            tuple(self.iterate_records(version)),
            self.hierarchy_nodes(version) if not self.legacy_metadata else (),
            version.source_version_id,
        )

    def hierarchy_node_counts(self, version_ids):
        if not version_ids:
            return {}
        counts = dict.fromkeys(version_ids, 0)
        counts.update(
            self.session.execute(
                select(
                    DatasetVersionHierarchyNode.version_id,
                    func.count(DatasetVersionHierarchyNode.id),
                )
                .where(DatasetVersionHierarchyNode.version_id.in_(version_ids))
                .group_by(DatasetVersionHierarchyNode.version_id)
            ).all()
        )
        return counts

    def hierarchy_nodes(self, version):
        nodes = self.session.scalars(
            select(DatasetVersionHierarchyNode)
            .where(DatasetVersionHierarchyNode.version_id == version.id)
            .order_by(DatasetVersionHierarchyNode.id)
        ).all()
        values = defaultdict(list)
        for value, field in self.session.execute(
            select(DatasetVersionHierarchyValue, DatasetVersionField)
            .join(
                DatasetVersionField,
                (DatasetVersionField.version_id == DatasetVersionHierarchyValue.version_id)
                & (DatasetVersionField.id == DatasetVersionHierarchyValue.field_id),
            )
            .where(DatasetVersionHierarchyValue.version_id == version.id)
            .order_by(DatasetVersionHierarchyValue.node_id, DatasetVersionHierarchyValue.field_id)
        ):
            values[value.node_id].append(hierarchy_value(field, value))
        return tuple(
            HierarchyNode(
                node.id, version.dataset_id, node.name, node.parent_id, tuple(values[node.id])
            )
            for node in nodes
        )

"""Canonical Record projections and complete hierarchy comparisons by stable identity."""

from sqlalchemy import and_, case, func, literal, or_, select, union_all

from refrncr.change_status import STATUSES, ChangeStatus
from refrncr.datatypes import get_datatype
from refrncr.diff_contracts import PAGE_SIZE, RecordProjection, ValueChange
from refrncr.hierarchy_contracts import hierarchy_node_paths
from refrncr.models import (
    Dataset,
    DatasetField,
    DatasetRecord,
    DatasetRecordValue,
    DatasetVersionField,
    DatasetVersionRecord,
    DatasetVersionValue,
)
from refrncr.record_identity import RecordIdentifierError, encode_record_identifier


class SqlAlchemyDiffOperations:
    def __init__(self, session, *, legacy_schema=False):
        self.session = session
        self._active_records = True if legacy_schema else DatasetRecord.deleted_content.is_(None)
        self._legacy_schema = legacy_schema
        self._hierarchy_kinds = {}

    def _is_hierarchy(self, dataset_id):
        if self._legacy_schema:
            return False
        if dataset_id not in self._hierarchy_kinds:
            dataset = self.session.get(Dataset, dataset_id)
            self._hierarchy_kinds[dataset_id] = dataset is not None and dataset.kind == "hierarchy"
        return self._hierarchy_kinds[dataset_id]

    def _hierarchy_content(self, dataset_id, version_id):
        from refrncr.hierarchy_persistence import SqlAlchemyHierarchyOperations
        from refrncr.persistence import SqlAlchemyDatasetFieldOperations
        from refrncr.version_persistence import SqlAlchemyVersionOperations

        versions = SqlAlchemyVersionOperations(self.session)
        version = versions.get(dataset_id, version_id) if version_id else None
        return (
            SqlAlchemyHierarchyOperations(self.session).list_for_dataset(dataset_id),
            versions.hierarchy_nodes(version) if version else (),
            tuple(SqlAlchemyDatasetFieldOperations(self.session).list_for_dataset(dataset_id)),
            versions.fields(version) if version else (),
        )

    def hierarchy_changes(self, dataset_id, version_id):
        from refrncr.diff_classification import classify_hierarchy

        if not self._is_hierarchy(dataset_id):
            return ()
        return classify_hierarchy(*self._hierarchy_content(dataset_id, version_id))

    def _hierarchy_records(self, dataset_id, version_id):
        from refrncr.hierarchy import hierarchy_leaf_records

        nodes, old_nodes, fields, old_fields = self._hierarchy_content(dataset_id, version_id)
        new, old = (
            {record.id: record for record in hierarchy_leaf_records(tree, schema)}
            for tree, schema in ((nodes, fields), (old_nodes, old_fields))
        )
        new_nodes, previous_nodes = (
            {node.id: node for node in tree} for tree in (nodes, old_nodes)
        )
        new_paths, old_paths = (hierarchy_node_paths(tree) for tree in (nodes, old_nodes))
        old_types = {field.id: field.data_type for field in old_fields}
        common = {field.id for field in fields if old_types.get(field.id) == field.data_type}
        for identity in sorted(new.keys() | old.keys()):
            before, after = old.get(identity), new.get(identity)
            left = (
                {value.field_id: value.canonical_key for value in before.values} if before else {}
            )
            right = {value.field_id: value.canonical_key for value in after.values} if after else {}
            changed = sum(left.get(key) != right.get(key) for key in common)
            structural = bool(
                before
                and after
                and (
                    new_nodes[identity].name != previous_nodes[identity].name
                    or new_nodes[identity].parent_id != previous_nodes[identity].parent_id
                )
            )
            if before is None or after is None or changed or structural:
                yield RecordProjection(
                    identity,
                    ChangeStatus.ADDED
                    if before is None
                    else ChangeStatus.REMOVED
                    if after is None
                    else ChangeStatus.CHANGED,
                    b"hierarchy:" + identity.encode() if before else None,
                    b"hierarchy:" + identity.encode() if after else None,
                    changed,
                    True,
                    old_paths[identity] if before else None,
                    new_paths[identity] if after else None,
                )

    def _hierarchy_values(self, dataset_id, version_id, record_id, status, page):
        nodes, old_nodes, fields, old_fields = self._hierarchy_content(dataset_id, version_id)
        node = next((item for item in nodes if item.id == record_id), None)
        previous = next((item for item in old_nodes if item.id == record_id), None)
        left = {value.field_id: value for value in previous.values} if previous else {}
        right = {value.field_id: value for value in node.values} if node else {}
        old_by_id = {field.id: field for field in old_fields}

        def rendered(value):
            if value is None:
                return None
            datatype = get_datatype(value.data_type)
            return datatype.text(datatype.from_key(value.canonical_key))

        changes = []
        for field in old_fields if status == ChangeStatus.REMOVED else fields:
            before, after = left.get(field.id), right.get(field.id)
            old_field = old_by_id.get(field.id)
            if status == ChangeStatus.CHANGED and (
                old_field is None
                or old_field.data_type != field.data_type
                or (before.canonical_key if before else None)
                == (after.canonical_key if after else None)
            ):
                continue
            changes.append(
                ValueChange(
                    field.id,
                    field.name,
                    old_field.data_type if old_field and status != ChangeStatus.ADDED else None,
                    field.data_type if status != ChangeStatus.REMOVED else None,
                    rendered(before) if status != ChangeStatus.ADDED else None,
                    rendered(after) if status != ChangeStatus.REMOVED else None,
                )
            )
        start = (page - 1) * PAGE_SIZE
        return tuple(changes[start : start + PAGE_SIZE]), len(changes)

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

    def _records(self, dataset_id, version_id):
        new = (
            select(DatasetRecord.id, DatasetRecord.identifier_key)
            .where(DatasetRecord.dataset_id == dataset_id, self._active_records)
            .subquery()
        )
        old = (
            select(DatasetVersionRecord.id, DatasetVersionRecord.identifier_key)
            .where(DatasetVersionRecord.version_id == version_id)
            .subquery()
        )
        changes = self._changes(dataset_id, version_id)
        current = (
            select(
                new.c.id,
                case((old.c.id.is_(None), ChangeStatus.ADDED), else_=ChangeStatus.CHANGED).label(
                    "status"
                ),
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
            select(
                old.c.id,
                literal(ChangeStatus.REMOVED),
                old.c.identifier_key,
                literal(None),
                literal(0),
            )
            .select_from(old.outerjoin(new, new.c.id == old.c.id))
            .where(new.c.id.is_(None))
        )
        return union_all(current, removed).subquery()

    def record_states(self, dataset_id, version_id, record_ids):
        if not record_ids:
            return {}
        if self._is_hierarchy(dataset_id):
            selected = set(record_ids)
            return {
                row.id: row.status
                for row in self._hierarchy_records(dataset_id, version_id)
                if row.id in selected
            }
        rows = self._records(dataset_id, version_id)
        return dict(
            self.session.execute(
                select(rows.c.id, rows.c.status).where(rows.c.id.in_(record_ids))
            ).all()
        )

    def removed_records(self, dataset_id, version):
        # Selection is the same REF-83/84 projection used for summary and review.
        from refrncr.version_persistence import SqlAlchemyVersionOperations

        if self._is_hierarchy(dataset_id):
            # A former leaf becoming a branch is not a deleted hierarchy node.
            return

        rows = self._records(dataset_id, version.id)
        ids = select(rows.c.id).where(rows.c.status == ChangeStatus.REMOVED)
        yield from SqlAlchemyVersionOperations(self.session).iterate_records(
            version, record_ids=ids
        )

    def counts(self, dataset_id, version_id):
        if self._is_hierarchy(dataset_id):
            counts = {status: 0 for status in STATUSES}
            for row in self._hierarchy_records(dataset_id, version_id):
                counts[row.status] += 1
            return tuple(counts.values())
        rows = self._records(dataset_id, version_id)
        counts = dict(
            self.session.execute(select(rows.c.status, func.count()).group_by(rows.c.status)).all()
        )
        return tuple(counts.get(status, 0) for status in STATUSES)

    def records(self, dataset_id, version_id, statuses, record_id=None):
        if self._is_hierarchy(dataset_id):
            for row in self._hierarchy_records(dataset_id, version_id):
                if row.status in statuses and (record_id is None or record_id == row.id):
                    yield row
            return
        rows = self._records(dataset_id, version_id)
        statement = select(rows).where(rows.c.status.in_(statuses))
        if record_id is not None:
            statement = statement.where(rows.c.id == record_id)
        # Scalar identifiers and counts only. Memory never depends on Record width.
        for row in self.session.execute(statement.execution_options(yield_per=100)):
            yield RecordProjection(row[0], ChangeStatus(row[1]), *row[2:])

    def validate_snapshot(self, version):
        """Validate stored content and identifiers, including the hierarchy leaf projection."""
        if self._is_hierarchy(version.dataset_id):
            from refrncr.hierarchy import hierarchy_leaf_records, hierarchy_validation_errors
            from refrncr.ports import DatasetBusinessKey
            from refrncr.services import _valid_identity
            from refrncr.version_persistence import SqlAlchemyVersionOperations

            versions = SqlAlchemyVersionOperations(self.session)
            fields = versions.fields(version)
            nodes = versions.hierarchy_nodes(version)
            if any(
                node.dataset_id != version.dataset_id
                or not _valid_identity(node.id)
                or (node.parent_id is not None and not _valid_identity(node.parent_id))
                for node in nodes
            ):
                return False
            identifier = DatasetBusinessKey(
                version.dataset_id, tuple(sorted(f.key for f in fields if f.is_identifier))
            )
            # Immutable publications may predate global node-name uniqueness.
            # Preserve their original sibling-name contract so they can be read
            # and restored for explicit repair; new publication validates globally.
            if len(fields) != version.field_count or hierarchy_validation_errors(
                fields, identifier, nodes, allow_legacy_names=True
            ):
                return False
            expected = hierarchy_leaf_records(nodes, fields)
            actual = tuple(versions.iterate_records(version))
            expected_identifiers = {}
            for record in expected:
                by_key = {value.field_key: value.canonical_key for value in record.values}
                expected_identifiers[record.id] = (
                    encode_record_identifier(
                        {key: by_key.get(key) for key in identifier.field_keys},
                        expected_keys=identifier.field_keys,
                    )
                    if identifier.field_keys
                    else b"hierarchy:" + record.id.encode()
                )
            actual_identifiers = dict(
                self.session.execute(
                    select(DatasetVersionRecord.id, DatasetVersionRecord.identifier_key).where(
                        DatasetVersionRecord.version_id == version.id
                    )
                ).all()
            )

            def content(records):
                return {
                    record.id: {
                        value.field_id: (value.data_type, value.canonical_key)
                        for value in record.values
                    }
                    for record in records
                }

            return (
                len(actual) == version.record_count
                and actual_identifiers == expected_identifiers
                and content(actual) == content(expected)
            )
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
        if self._is_hierarchy(dataset_id):
            # Concept review must remain available for incomplete content and for
            # restored conflicts. Canonical storage is decoded by this adapter.
            self._hierarchy_content(dataset_id, None)
            return True
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
            .where(DatasetRecord.dataset_id == dataset_id, self._active_records)
            .order_by(DatasetRecord.id, DatasetField.key)
        )
        return self._validate_rows(statement, identifier_fields)

    def _validate_rows(self, statement, identifier_fields, expected_count=None):
        from uuid import UUID

        from refrncr.datatypes import DatatypeError

        record_id, encoded, components, count = None, None, [], 0

        def valid_identifier():
            if tuple(key for key, _value in components) != identifier_fields:
                return False
            try:
                return encoded == encode_record_identifier(
                    components, expected_keys=identifier_fields
                )
            except RecordIdentifierError:
                return False

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
        return (expected_count is None or count == expected_count) and (
            record_id is None or valid_identifier()
        )

    def values(self, dataset_id, version_id, record_id, status, page):
        if self._is_hierarchy(dataset_id):
            return self._hierarchy_values(dataset_id, version_id, record_id, status, page)
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
        if status == ChangeStatus.REMOVED:
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
                if status == ChangeStatus.CHANGED
                else literal(None).label("before_type"),
                new.c.data_type.label("after_type"),
                ov.c.value.label("before")
                if status == ChangeStatus.CHANGED
                else literal(None).label("before"),
                nv.c.value.label("after"),
                new.c.display_position,
            ).select_from(
                new.outerjoin(old, old.c.id == new.c.id)
                .outerjoin(nv, nv.c.field_id == new.c.id)
                .outerjoin(ov, ov.c.field_id == new.c.id)
            )
            if status == ChangeStatus.CHANGED:
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

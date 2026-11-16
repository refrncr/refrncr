from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import (
    MetaData,
    Table,
    delete,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from refrncr.persistence.common import clock_now, locked, safe_constraint_name
from refrncr.persistence.models import (
    Dataset,
    DatasetBusinessKeyField,
    DatasetExportAccess,
    DatasetField,
    DatasetFieldSourceFormat,
    DatasetHierarchyNode,
    DatasetHierarchyValue,
    DatasetRecord,
    DatasetRecordValue,
    DatasetVersion,
    Grant,
    new_id,
)
from refrncr.ports import (
    DatasetBusinessKey,
    DatasetFieldRecord,
    DatasetOverview,
    PersistenceConflictError,
)


class SqlAlchemyDatasetOperations:
    def deletion_counts(self, dataset_id: str) -> tuple[int, int, int, int]:
        def total(model):
            return self.session.scalar(
                select(func.count()).select_from(model).where(model.dataset_id == dataset_id)
            )

        return (
            total(DatasetField),
            (
                total(DatasetHierarchyNode)
                if self.session.scalar(select(Dataset.kind).where(Dataset.id == dataset_id))
                == "hierarchy"
                else total(DatasetRecord)
            ),
            total(DatasetVersion),
            total(DatasetExportAccess),
        )

    def delete_with_dependents(self, dataset_id: str) -> None:
        # REF-141 inventory: values RESTRICT Field deletion. Do not rely on
        # unspecified ordering of multiple database cascades from Dataset.
        for model in (
            DatasetVersion,
            DatasetHierarchyValue,
            DatasetHierarchyNode,
            DatasetRecordValue,
            DatasetRecord,
            DatasetBusinessKeyField,
            DatasetField,
            DatasetExportAccess,
        ):
            self.session.execute(
                delete(model)
                .where(model.dataset_id == dataset_id)
                .execution_options(synchronize_session=False)
            )
        self.session.execute(
            delete(Grant)
            .where(Grant.resource_type == "dataset", Grant.resource_id == dataset_id)
            .execution_options(synchronize_session=False)
        )
        result = self.session.execute(
            delete(Dataset)
            .where(Dataset.id == dataset_id)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise PersistenceConflictError()

    def __init__(self, session: Session) -> None:
        self.session = session

    def owns_datasets(self, team_id: str) -> bool:
        return (
            self.session.scalar(select(Dataset.id).where(Dataset.owner_team_id == team_id).limit(1))
            is not None
        )

    def change_owner_if_expected(self, dataset_id, expected_owner_team_id, owner_team_id) -> bool:
        result = self.session.execute(
            update(Dataset)
            .where(Dataset.id == dataset_id, Dataset.owner_team_id == expected_owner_team_id)
            .values(owner_team_id=owner_team_id, updated_at=clock_now(self.session))
        )
        if result.rowcount == 1:
            self.session.scalar(
                select(Dataset)
                .where(Dataset.id == dataset_id)
                .execution_options(populate_existing=True)
            )
        return result.rowcount == 1

    def rename_key(self, dataset_id: str, key: str) -> None:
        """Controlled key correction; lifecycle authorization belongs to the service."""
        try:
            result = self.session.execute(
                update(Dataset)
                .where(Dataset.id == dataset_id)
                .values(_key=key)
                .execution_options(synchronize_session="fetch")
            )
        except IntegrityError as exc:
            raise PersistenceConflictError(safe_constraint_name(exc)) from None
        if result.rowcount != 1:
            raise PersistenceConflictError()

    def uses_linked_field(self, dataset_id: str, field_key: str) -> bool:
        return (
            self.session.scalar(
                select(Dataset.id)
                .where(
                    (Dataset.source_dataset_id == dataset_id)
                    & (Dataset.source_field_key == field_key)
                    | (Dataset.target_dataset_id == dataset_id)
                    & (Dataset.target_field_key == field_key)
                )
                .limit(1)
            )
            is not None
        )

    def current_overview(self, dataset_ids: tuple[str, ...] | None) -> list[DatasetOverview]:
        if dataset_ids == ():
            return []
        # DatasetRecord contains only the mutable working set. Future version storage
        # must preserve this boundary, never union historical/published rows here.
        current_counts = select(
            DatasetRecord.dataset_id, func.count(DatasetRecord.id).label("record_count")
        ).where(DatasetRecord.deleted_content.is_(None))
        hierarchy_counts = select(
            DatasetHierarchyNode.dataset_id,
            func.count(DatasetHierarchyNode.id).label("record_count"),
        )
        if dataset_ids is not None:
            current_counts = current_counts.where(DatasetRecord.dataset_id.in_(dataset_ids))
            hierarchy_counts = hierarchy_counts.where(
                DatasetHierarchyNode.dataset_id.in_(dataset_ids)
            )
        hierarchy_counts = hierarchy_counts.group_by(DatasetHierarchyNode.dataset_id)
        current_counts = (
            current_counts.group_by(DatasetRecord.dataset_id).union_all(hierarchy_counts).subquery()
        )
        statement = (
            select(Dataset, func.coalesce(current_counts.c.record_count, 0))
            .outerjoin(current_counts, current_counts.c.dataset_id == Dataset.id)
            .order_by(Dataset._key)
        )
        if dataset_ids is not None:
            statement = statement.where(Dataset.id.in_(dataset_ids))
        return [
            DatasetOverview(
                dataset,
                count,
                dataset.content_updated_at or dataset.created_at,
                dataset.owner_team_name,
            )
            for dataset, count in self.session.execute(statement)
        ]

    def get(self, dataset_id: str, *, for_update: bool = False) -> Dataset | None:
        statement = locked(
            select(Dataset).where(Dataset.id == dataset_id), self.session, for_update
        )
        return self.session.scalar(statement)

    def find_by_key(self, key: str, *, for_update: bool = False) -> Dataset | None:
        statement = locked(select(Dataset).where(Dataset._key == key), self.session, for_update)
        return self.session.scalar(statement)

    def list_all(self) -> list[Dataset]:
        return list(self.session.scalars(select(Dataset).order_by(Dataset._key)))

    def mappings_using(self, source_dataset_id: str) -> list[Dataset]:
        return list(
            self.session.scalars(
                select(Dataset)
                .where(Dataset.kind == "mapping", Dataset.source_dataset_id == source_dataset_id)
                .order_by(Dataset.name, Dataset._key)
            )
        )

    def mapping_usage_count(self, source_dataset_id: str) -> int:
        return self.session.scalar(
            select(func.count())
            .select_from(Dataset)
            .where(Dataset.kind == "mapping", Dataset.source_dataset_id == source_dataset_id)
        )

    def list_by_ids(self, dataset_ids: tuple[str, ...]) -> list[Dataset]:
        if not dataset_ids:
            return []
        return list(
            self.session.scalars(
                select(Dataset).where(Dataset.id.in_(dataset_ids)).order_by(Dataset._key)
            )
        )

    def exists(self, dataset_id: str) -> bool:
        return self.session.scalar(select(Dataset.id).where(Dataset.id == dataset_id)) is not None

    def create(self, **values) -> Dataset:
        dataset = Dataset(_key=values.pop("key"), **values)
        self.session.add(dataset)
        return dataset


class SqlAlchemyDatasetFieldOperations:
    def __init__(self, session: Session, *, legacy_metadata=False) -> None:
        self.session = session
        self.legacy_metadata = legacy_metadata

    def find_by_key(
        self, dataset_id: str, key: str, *, for_update: bool = False
    ) -> DatasetField | None:
        if not self._has_display_position():
            row = self.session.execute(
                select(
                    DatasetField.id,
                    DatasetField.dataset_id,
                    DatasetField._key,
                    DatasetField.name,
                    DatasetField.data_type,
                    DatasetField.is_required,
                    DatasetField.is_unique,
                    DatasetField.created_at,
                    DatasetField.updated_at,
                ).where(DatasetField.dataset_id == dataset_id, DatasetField._key == key)
            ).one_or_none()
            if row is None:
                return None
            values = dict(row._mapping)
            values["key"] = values.pop("_key")
            return SimpleNamespace(**values)
        statement = locked(
            select(DatasetField).where(
                DatasetField.dataset_id == dataset_id,
                DatasetField._key == key,
            ),
            self.session,
            for_update,
        )
        return self.session.scalar(statement)

    def list_for_dataset(self, dataset_id: str) -> list[DatasetField]:
        if self.legacy_metadata:
            table = Table("dataset_fields", MetaData(), autoload_with=self.session.connection())
            return [
                SimpleNamespace(definition=None, unit=None, **row)
                for row in self.session.execute(
                    select(table)
                    .where(table.c.dataset_id == dataset_id)
                    .order_by(
                        table.c.display_position if "display_position" in table.c else table.c.key
                    )
                ).mappings()
            ]
        if not self._has_display_position():
            rows = self.session.execute(
                select(
                    DatasetField.id,
                    DatasetField.dataset_id,
                    DatasetField._key,
                    DatasetField.name,
                    DatasetField.data_type,
                    DatasetField.is_required,
                    DatasetField.is_unique,
                    DatasetField.created_at,
                    DatasetField.updated_at,
                )
                .where(DatasetField.dataset_id == dataset_id)
                .order_by(DatasetField._key)
            )
            result = []
            for row in rows:
                values = dict(row._mapping)
                values["key"] = values.pop("_key")
                result.append(SimpleNamespace(**values))
            return result
        return list(
            self.session.scalars(
                select(DatasetField)
                .where(DatasetField.dataset_id == dataset_id)
                .order_by(DatasetField.display_position, DatasetField._key, DatasetField.id)
            )
        )

    def list_for_datasets(self, dataset_ids: tuple[str, ...]) -> dict[str, list[DatasetField]]:
        if not dataset_ids:
            return {}
        if self.legacy_metadata:
            table = Table("dataset_fields", MetaData(), autoload_with=self.session.connection())
            position = table.c.display_position if "display_position" in table.c else table.c.key
            rows = list(
                self.session.execute(
                    select(table)
                    .where(table.c.dataset_id.in_(dataset_ids))
                    .order_by(table.c.dataset_id, position, table.c.key)
                ).mappings()
            )
            return {
                dataset_id: [
                    SimpleNamespace(definition=None, unit=None, **row) for row in rows_for_dataset
                ]
                for dataset_id in dataset_ids
                for rows_for_dataset in ([row for row in rows if row["dataset_id"] == dataset_id],)
            }
        rows = self.session.scalars(
            select(DatasetField)
            .where(DatasetField.dataset_id.in_(dataset_ids))
            .order_by(DatasetField.dataset_id, DatasetField.display_position, DatasetField._key)
        )
        result = {dataset_id: [] for dataset_id in dataset_ids}
        for field in rows:
            result[field.dataset_id].append(field)
        return result

    def create(self, **values) -> DatasetField:
        if not self._has_display_position():
            values.pop("display_position", None)
            values["id"] = new_id()
            values["created_at"] = values["updated_at"] = clock_now(self.session)
            key = values.pop("key")
            self.session.execute(
                text(
                    "INSERT INTO dataset_fields (id, dataset_id, key, name, data_type, "
                    "is_required, is_unique, created_at, updated_at) VALUES (:id, :dataset_id, "
                    ":key, :name, :data_type, :is_required, :is_unique, :created_at, :updated_at)"
                ),
                {**values, "key": key},
            )
            return SimpleNamespace(_key=key, key=key, **values)
        field = DatasetField(_key=values.pop("key"), **values)
        self.session.add(field)
        return field

    def delete(self, field: DatasetFieldRecord) -> None:
        self.session.execute(
            delete(DatasetField)
            .where(DatasetField.id == field.id)
            .execution_options(synchronize_session=False)
        )
        self.session.expire_all()

    def replace_order(self, dataset_id: str, field_ids: tuple[str, ...]) -> None:
        fields = self.list_for_dataset(dataset_id)
        by_id = {field.id: field for field in fields}
        if set(by_id) != set(field_ids) or len(field_ids) != len(by_id):
            raise PersistenceConflictError()
        offset = max((field.display_position for field in fields), default=0) + len(fields) + 1
        for index, field_id in enumerate(field_ids, 1):
            by_id[field_id].display_position = offset + index
        self.session.flush()
        for index, field_id in enumerate(field_ids, 1):
            by_id[field_id].display_position = index
        self.session.flush()

    def rename_key(self, dataset_id: str, field_id: str, key: str) -> DatasetField:
        """Controlled pre-publication correction; lifecycle checks belong to the service."""
        try:
            result = self.session.execute(
                update(DatasetField)
                .where(DatasetField.dataset_id == dataset_id, DatasetField.id == field_id)
                .values(_key=key, updated_at=clock_now(self.session))
                .execution_options(synchronize_session=False)
            )
        except IntegrityError as exc:
            raise PersistenceConflictError(safe_constraint_name(exc)) from None
        if result.rowcount != 1:
            raise PersistenceConflictError()
        self.session.expire_all()
        field = self.find_by_key(dataset_id, key, for_update=True)
        if field is None:
            raise PersistenceConflictError()
        return field

    def source_formats(self, dataset_id):
        return {
            key: dict(choice)
            for key, choice in self.session.execute(
                select(DatasetField._key, DatasetFieldSourceFormat.choice)
                .join(
                    DatasetFieldSourceFormat, DatasetField.id == DatasetFieldSourceFormat.field_id
                )
                .where(DatasetField.dataset_id == dataset_id)
            )
        }

    def remember_source_formats(self, dataset_id, choices):
        for field in self.list_for_dataset(dataset_id):
            if field.key not in choices:
                continue
            proposal = self.session.get(DatasetFieldSourceFormat, field.id)
            if proposal is None:
                self.session.add(
                    DatasetFieldSourceFormat(field_id=field.id, choice=dict(choices[field.key]))
                )
            else:
                proposal.choice = dict(choices[field.key])

    def _has_display_position(self) -> bool:
        return "display_position" in {
            column["name"]
            for column in inspect(self.session.connection()).get_columns("dataset_fields")
        }


class SqlAlchemyDatasetBusinessKeyOperations:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, dataset_id: str) -> DatasetBusinessKey:
        field_key = DatasetBusinessKeyField.field_key
        if self.session.get_bind().dialect.name == "postgresql":
            field_key = field_key.collate("C")
        keys = self.session.scalars(
            select(DatasetBusinessKeyField.field_key)
            .where(DatasetBusinessKeyField.dataset_id == dataset_id)
            .order_by(field_key)
        )
        return DatasetBusinessKey(dataset_id=dataset_id, field_keys=tuple(keys))

    def contains(self, dataset_id: str, field_key: str) -> bool:
        return (
            self.session.scalar(
                select(DatasetBusinessKeyField.field_key).where(
                    DatasetBusinessKeyField.dataset_id == dataset_id,
                    DatasetBusinessKeyField.field_key == field_key,
                )
            )
            is not None
        )

    def replace(self, dataset_id: str, field_keys: tuple[str, ...]) -> None:
        self.session.execute(
            delete(DatasetBusinessKeyField).where(DatasetBusinessKeyField.dataset_id == dataset_id)
        )
        self.session.add_all(
            DatasetBusinessKeyField(dataset_id=dataset_id, field_key=field_key)
            for field_key in field_keys
        )

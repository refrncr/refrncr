"""Active concept storage and bounded, identity-preserving snapshot initialization."""

from sqlalchemy import delete, insert, literal, select, update

from refrncr.concept_contracts import ActiveConcept
from refrncr.models import (
    DatasetBusinessKeyField,
    DatasetConcept,
    DatasetField,
    DatasetRecord,
    DatasetRecordValue,
    DatasetVersionField,
    DatasetVersionRecord,
    DatasetVersionValue,
    new_id,
)


class SqlAlchemyConceptOperations:
    def __init__(self, session):
        self.session = session

    @staticmethod
    def _view(row):
        return ActiveConcept(*(getattr(row, name) for name in ActiveConcept.__dataclass_fields__))

    def get(self, dataset_id):
        row = self.session.get(DatasetConcept, dataset_id)
        return self._view(row) if row else None

    def list_for_datasets(self, dataset_ids):
        if not dataset_ids:
            return {}
        return {
            row.dataset_id: self._view(row)
            for row in self.session.scalars(
                select(DatasetConcept).where(DatasetConcept.dataset_id.in_(dataset_ids))
            )
        }

    def create(self, dataset_id, base_version_id, creator_id, now, revision):
        row = DatasetConcept(
            id=new_id(),
            dataset_id=dataset_id,
            base_version_id=base_version_id,
            creator_id=creator_id,
            created_at=now,
            updated_at=now,
            start_revision=revision,
        )
        self.session.add(row)
        self.session.flush()
        return self._view(row)

    def touch(self, dataset_id, now):
        self.session.execute(
            update(DatasetConcept)
            .where(DatasetConcept.dataset_id == dataset_id)
            .values(updated_at=now)
        )

    def close(self, dataset_id):
        self.session.execute(delete(DatasetConcept).where(DatasetConcept.dataset_id == dataset_id))

    def initialize(self, dataset, version, now):
        # Caller owns the Dataset lock and has established there is no active concept.
        # Preserve source UUIDs; INSERT SELECT avoids materializing any Record/value graph.
        for model in (DatasetRecordValue, DatasetRecord, DatasetBusinessKeyField, DatasetField):
            self.session.execute(delete(model).where(model.dataset_id == dataset.id))
        field = DatasetVersionField
        self.session.execute(
            insert(DatasetField.__table__).from_select(
                [
                    "id",
                    "dataset_id",
                    "key",
                    "name",
                    "data_type",
                    "is_required",
                    "is_unique",
                    "display_position",
                    "created_at",
                    "updated_at",
                ],
                select(
                    field.id,
                    literal(dataset.id),
                    field.key,
                    field.name,
                    field.data_type,
                    field.is_required,
                    field.is_unique,
                    field.display_position,
                    literal(now),
                    literal(now),
                ).where(field.version_id == version.id),
            )
        )
        self.session.execute(
            insert(DatasetBusinessKeyField).from_select(
                ["dataset_id", "field_key"],
                select(literal(dataset.id), field.key).where(
                    field.version_id == version.id, field.is_identifier.is_(True)
                ),
            )
        )
        record = DatasetVersionRecord
        self.session.execute(
            insert(DatasetRecord).from_select(
                ["id", "dataset_id", "identifier_key", "revision", "created_at", "updated_at"],
                select(
                    record.id,
                    literal(dataset.id),
                    record.identifier_key,
                    literal(dataset.revision + 1),
                    literal(now),
                    literal(now),
                ).where(record.version_id == version.id),
            )
        )
        value = DatasetVersionValue
        self.session.execute(
            insert(DatasetRecordValue).from_select(
                [
                    "record_id",
                    "field_id",
                    "dataset_id",
                    "string_value",
                    "canonical_value_key",
                    "unique_value_key",
                ],
                select(
                    value.record_id,
                    value.field_id,
                    literal(dataset.id),
                    value.canonical_text,
                    value.canonical_key,
                    value.unique_key,
                ).where(value.version_id == version.id),
            )
        )
        dataset.name, dataset.description = version.name, version.description
        dataset.content_updated_at = now

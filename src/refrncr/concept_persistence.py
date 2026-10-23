"""Active concept storage and bounded, identity-preserving snapshot initialization."""

from sqlalchemy import Date, Numeric, case, cast, delete, insert, literal, select, update

from refrncr.concept_contracts import ActiveConcept
from refrncr.models import (
    DatasetBusinessKeyField,
    DatasetConcept,
    DatasetField,
    DatasetFieldSourceFormat,
    DatasetHierarchyNode,
    DatasetHierarchyValue,
    DatasetRecord,
    DatasetRecordValue,
    DatasetVersionField,
    DatasetVersionHierarchyNode,
    DatasetVersionHierarchyValue,
    DatasetVersionRecord,
    DatasetVersionValue,
    new_id,
)


class SqlAlchemyConceptOperations:
    def __init__(self, session, *, legacy_schema=False):
        self.session = session
        self._legacy_schema = legacy_schema

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
        if self._legacy_schema:
            # REF-84 runs before REF-160 adds Concept import metadata.
            from sqlalchemy import MetaData, Table

            table = Table("dataset_concepts", MetaData(), autoload_with=self.session.connection())
            self.session.execute(
                table.insert().values(
                    id=new_id(),
                    dataset_id=dataset_id,
                    base_version_id=base_version_id,
                    creator_id=creator_id,
                    created_at=now,
                    updated_at=now,
                    start_revision=revision,
                )
            )
            return None
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

    def record_mutation(self, dataset_id, *, import_result=None):
        from sqlalchemy import case

        changes = {
            "records_mutated": case((DatasetConcept.records_mutated.is_(None), None), else_=True)
        }
        if import_result is not None:
            changes["import_result"] = dict(import_result)
        self.session.execute(
            update(DatasetConcept).where(DatasetConcept.dataset_id == dataset_id).values(**changes)
        )

    def close(self, dataset_id):
        self.session.execute(delete(DatasetConcept).where(DatasetConcept.dataset_id == dataset_id))

    def discard(self, dataset, current):
        # Explicit child-first deletion; snapshot UUIDs do not reference working rows.
        self.session.execute(
            delete(DatasetFieldSourceFormat).where(
                DatasetFieldSourceFormat.field_id.in_(
                    select(DatasetField.id).where(DatasetField.dataset_id == dataset.id)
                )
            )
        )
        hierarchy_models = (
            (DatasetHierarchyValue, DatasetHierarchyNode)
            if getattr(dataset, "kind", "reference") == "hierarchy"
            else ()
        )
        for model in (
            *hierarchy_models,
            DatasetRecordValue,
            DatasetRecord,
            DatasetBusinessKeyField,
            DatasetField,
        ):
            self.session.execute(delete(model).where(model.dataset_id == dataset.id))
        self.close(dataset.id)
        dataset.content_updated_at = current.published_at
        dataset.source_version_id = current.source_version_id

    def initialize(self, dataset, version, now):
        # Caller owns the Dataset lock and has established there is no active concept.
        # Import proposals are installation metadata, not versioned Field configuration.
        # Preserve them only for the exact Field identities retained by this snapshot.
        proposals = [
            {"field_id": field_id, "choice": choice}
            for field_id, choice in self.session.execute(
                select(DatasetFieldSourceFormat.field_id, DatasetFieldSourceFormat.choice)
                .join(
                    DatasetVersionField, DatasetVersionField.id == DatasetFieldSourceFormat.field_id
                )
                .where(DatasetVersionField.version_id == version.id)
            )
        ]
        # Preserve source UUIDs; INSERT SELECT avoids materializing any Record/value graph.
        hierarchy_models = (
            (DatasetHierarchyValue, DatasetHierarchyNode)
            if getattr(dataset, "kind", "reference") == "hierarchy"
            else ()
        )
        for model in (
            *hierarchy_models,
            DatasetRecordValue,
            DatasetRecord,
            DatasetBusinessKeyField,
            DatasetField,
        ):
            self.session.execute(delete(model).where(model.dataset_id == dataset.id))
        field = DatasetVersionField
        mapping_columns, mapping_values = [], []
        if getattr(dataset, "kind", "reference") == "mapping":
            from sqlalchemy.orm import aliased

            source_field = aliased(DatasetVersionField)
            mapping_columns = ["mapping_role", "source_field_key", "source_field_id"]
            mapping_values = [
                field.mapping_role,
                field.source_field_key,
                select(source_field.id)
                .where(
                    source_field.version_id == version.source_version_id,
                    source_field.key == field.source_field_key,
                )
                .correlate(field)
                .scalar_subquery(),
            ]
        self.session.execute(
            insert(DatasetField.__table__).from_select(
                [
                    "id",
                    "dataset_id",
                    "key",
                    "name",
                    "definition",
                    "unit",
                    "data_type",
                    "is_required",
                    "is_unique",
                    "display_position",
                    "hierarchy_scope",
                    *mapping_columns,
                    "created_at",
                    "updated_at",
                ],
                select(
                    field.id,
                    literal(dataset.id),
                    field.key,
                    field.name,
                    field.definition,
                    field.unit,
                    field.data_type,
                    field.is_required,
                    field.is_unique,
                    field.display_position,
                    field.hierarchy_scope,
                    *mapping_values,
                    literal(now),
                    literal(now),
                ).where(field.version_id == version.id),
            )
        )
        if proposals:
            self.session.execute(insert(DatasetFieldSourceFormat), proposals)
        self.session.execute(
            insert(DatasetBusinessKeyField).from_select(
                ["dataset_id", "field_key"],
                select(literal(dataset.id), field.key).where(
                    field.version_id == version.id, field.is_identifier.is_(True)
                ),
            )
        )
        if getattr(dataset, "kind", "reference") == "hierarchy":
            node = DatasetVersionHierarchyNode
            self.session.execute(
                insert(DatasetHierarchyNode).from_select(
                    ["id", "dataset_id", "name", "parent_id", "revision"],
                    select(
                        node.id,
                        literal(dataset.id),
                        node.name,
                        node.parent_id,
                        literal(dataset.revision + 1),
                    ).where(node.version_id == version.id),
                )
            )
            value = DatasetVersionHierarchyValue
            self.session.execute(
                insert(DatasetHierarchyValue).from_select(
                    ["node_id", "field_id", "dataset_id", "canonical_key", "canonical_text"],
                    select(
                        value.node_id,
                        value.field_id,
                        literal(dataset.id),
                        value.canonical_key,
                        value.canonical_text,
                    ).where(value.version_id == version.id),
                )
            )
            dataset.content_updated_at = now
            return
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
        date_value = value.canonical_text
        if self.session.get_bind().dialect.name != "sqlite":
            date_value = cast(value.canonical_text, Date)
        self.session.execute(
            insert(DatasetRecordValue).from_select(
                [
                    "record_id",
                    "field_id",
                    "dataset_id",
                    "string_value",
                    "number_value",
                    "boolean_value",
                    "date_value",
                    "canonical_value_key",
                    "unique_value_key",
                ],
                select(
                    value.record_id,
                    value.field_id,
                    literal(dataset.id),
                    case(
                        (
                            field.data_type.notin_(("number", "boolean", "date")),
                            value.canonical_text,
                        )
                    ),
                    case(
                        (
                            field.data_type == "number",
                            cast(value.canonical_text, Numeric(38, 18)),
                        )
                    ),
                    case((field.data_type == "boolean", value.canonical_text == "true")),
                    case((field.data_type == "date", date_value)),
                    value.canonical_key,
                    value.unique_key,
                )
                .join(
                    field,
                    (field.version_id == value.version_id) & (field.id == value.field_id),
                )
                .where(value.version_id == version.id),
            )
        )
        dataset.content_updated_at = now

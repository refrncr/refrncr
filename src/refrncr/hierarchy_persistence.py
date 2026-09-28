"""Hierarchy storage; callers own the authorized, serialized Dataset transaction."""

from collections import defaultdict

from sqlalchemy import delete, select

from refrncr.datatypes import DatatypeError, get_datatype
from refrncr.hierarchy_contracts import HierarchyNode
from refrncr.models import DatasetField, DatasetHierarchyNode, DatasetHierarchyValue
from refrncr.ports import PersistenceConflictError, RecordValue


def hierarchy_value(field, value):
    datatype = get_datatype(field.data_type)
    decoded = datatype.from_key(value.canonical_key)
    if datatype.text(decoded) != value.canonical_text:
        raise DatatypeError("Invalid stored canonical value", "invalid_storage")
    return RecordValue(
        field.id, field.key, field.data_type, decoded, value.canonical_key, field.is_unique
    )


class SqlAlchemyHierarchyOperations:
    def __init__(self, session):
        self.session = session

    def list_for_dataset(self, dataset_id):
        nodes = self.session.scalars(
            select(DatasetHierarchyNode)
            .where(DatasetHierarchyNode.dataset_id == dataset_id)
            .order_by(DatasetHierarchyNode.id)
        ).all()
        values = defaultdict(list)
        for value, field in self.session.execute(
            select(DatasetHierarchyValue, DatasetField)
            .join(
                DatasetField,
                (DatasetField.id == DatasetHierarchyValue.field_id)
                & (DatasetField.dataset_id == DatasetHierarchyValue.dataset_id),
            )
            .where(DatasetHierarchyValue.dataset_id == dataset_id)
            .order_by(DatasetHierarchyValue.node_id, DatasetHierarchyValue.field_id)
        ):
            values[value.node_id].append(hierarchy_value(field, value))
        return tuple(
            HierarchyNode(
                node.id,
                dataset_id,
                node.name,
                node.parent_id,
                tuple(values[node.id]),
                node.revision,
            )
            for node in nodes
        )

    def get(self, dataset_id, node_id):
        return next(
            (node for node in self.list_for_dataset(dataset_id) if node.id == node_id), None
        )

    def create(self, dataset_id, name, parent_id):
        row = DatasetHierarchyNode(dataset_id=dataset_id, name=name, parent_id=parent_id)
        self.session.add(row)
        self.session.flush()
        return HierarchyNode(row.id, dataset_id, name, parent_id, (), row.revision)

    def update(self, dataset_id, node_id, name, parent_id):
        row = self.session.scalar(
            select(DatasetHierarchyNode).where(
                DatasetHierarchyNode.dataset_id == dataset_id, DatasetHierarchyNode.id == node_id
            )
        )
        if row is None:
            raise PersistenceConflictError()
        row.name, row.parent_id = name, parent_id
        row.revision += 1
        self.session.flush()
        return self.get(dataset_id, node_id)

    def replace_values(self, dataset_id, node_id, values):
        row = self.session.scalar(
            select(DatasetHierarchyNode).where(
                DatasetHierarchyNode.dataset_id == dataset_id, DatasetHierarchyNode.id == node_id
            )
        )
        if row is None:
            raise PersistenceConflictError()
        self.session.execute(
            delete(DatasetHierarchyValue).where(
                DatasetHierarchyValue.dataset_id == dataset_id,
                DatasetHierarchyValue.node_id == node_id,
            )
        )
        for value in values:
            datatype = get_datatype(value.data_type)
            self.session.add(
                DatasetHierarchyValue(
                    dataset_id=dataset_id,
                    node_id=node_id,
                    field_id=value.field_id,
                    canonical_key=value.canonical_key,
                    canonical_text=datatype.text(datatype.from_key(value.canonical_key)),
                )
            )
        row.revision += 1
        self.session.flush()
        return self.get(dataset_id, node_id)

    def delete(self, dataset_id, node_id):
        result = self.session.execute(
            delete(DatasetHierarchyNode).where(
                DatasetHierarchyNode.dataset_id == dataset_id, DatasetHierarchyNode.id == node_id
            )
        )
        if result.rowcount != 1:
            raise PersistenceConflictError()

    def field_has_values(self, dataset_id, field_id):
        return (
            self.session.scalar(
                select(DatasetHierarchyValue.node_id)
                .where(
                    DatasetHierarchyValue.dataset_id == dataset_id,
                    DatasetHierarchyValue.field_id == field_id,
                )
                .limit(1)
            )
            is not None
        )

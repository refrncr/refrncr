from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from refrncr.db.base import Base
from refrncr.db.types import UTCDateTime
from refrncr.domain import DATASET_FIELD_TYPES, MAX_RECORD_IDENTIFIER_BYTES
from refrncr.ports import PersistenceConflictError


def new_id() -> str:
    return str(uuid4())


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "number", name="uq_dataset_versions_number"),
        Index(
            "uq_dataset_versions_effective_date",
            "dataset_id",
            "effective_date",
            unique=True,
            sqlite_where=text("cancelled_at IS NULL"),
            postgresql_where=text("cancelled_at IS NULL"),
        ),
        Index("uq_dataset_versions_dataset_id", "dataset_id", "id", unique=True),
        CheckConstraint("number >= 1", name="ck_dataset_versions_number"),
        CheckConstraint("length(release_note) <= 2000", name="ck_dataset_versions_note"),
        CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_dataset_versions_name"),
        CheckConstraint("length(description) <= 10000", name="ck_dataset_versions_description"),
        CheckConstraint(
            "(cancelled_at IS NULL AND cancelling_actor_id IS NULL) OR "
            "(cancelled_at IS NOT NULL AND cancelling_actor_id IS NOT NULL)",
            name="ck_dataset_versions_cancellation",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    source_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT", name="fk_versions_source_version"),
        nullable=True,
        deferred=True,
    )
    number: Mapped[int] = mapped_column(Integer)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime())
    publisher_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    effective_date: Mapped[date] = mapped_column(Date)
    release_note: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    field_count: Mapped[int] = mapped_column(Integer)
    record_count: Mapped[int] = mapped_column(Integer)
    fields_added: Mapped[int] = mapped_column(Integer)
    fields_changed: Mapped[int] = mapped_column(Integer)
    fields_removed: Mapped[int] = mapped_column(Integer)
    records_added: Mapped[int] = mapped_column(Integer)
    records_changed: Mapped[int] = mapped_column(Integer)
    records_removed: Mapped[int] = mapped_column(Integer)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelling_actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )


class DatasetVersionField(Base):
    __tablename__ = "dataset_version_fields"
    __table_args__ = (
        CheckConstraint(
            "hierarchy_scope IS NULL OR hierarchy_scope IN ('leaves', 'all_nodes')",
            name="ck_version_fields_hierarchy_scope",
        ),
        CheckConstraint("length(definition) <= 4000", name="ck_version_fields_definition"),
        CheckConstraint("length(unit) <= 64", name="ck_version_fields_unit"),
        UniqueConstraint("version_id", "key", name="uq_version_fields_key"),
        UniqueConstraint("version_id", "display_position", name="uq_version_fields_position"),
        CheckConstraint("display_position >= 1", name="ck_version_fields_position"),
        CheckConstraint("length(key) BETWEEN 1 AND 100", name="ck_version_fields_key"),
        CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_version_fields_name"),
        CheckConstraint("NOT is_identifier OR is_required", name="ck_version_fields_identifier"),
        CheckConstraint(
            "data_type IN (" + ", ".join(repr(t) for t in DATASET_FIELD_TYPES) + ")",
            name="ck_version_fields_type",
        ),
        CheckConstraint(
            "mapping_role IS NULL OR mapping_role IN "
            "('source_identifier', 'source_context', 'mapping_target')",
            name="ck_version_fields_mapping_role",
        ),
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    definition: Mapped[str | None] = mapped_column(String(4000))
    unit: Mapped[str | None] = mapped_column(String(64))
    data_type: Mapped[str] = mapped_column(String(20))
    is_required: Mapped[bool] = mapped_column(Boolean)
    is_unique: Mapped[bool] = mapped_column(Boolean)
    display_position: Mapped[int] = mapped_column(Integer)
    is_identifier: Mapped[bool] = mapped_column(Boolean)
    hierarchy_scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mapping_role: Mapped[str | None] = mapped_column(String(24), nullable=True)
    source_field_key: Mapped[str | None] = mapped_column(String(100), nullable=True)


class DatasetVersionRecord(Base):
    __tablename__ = "dataset_version_records"
    __table_args__ = (
        UniqueConstraint("version_id", "identifier_key", name="uq_version_records_identifier"),
        CheckConstraint(
            f"length(identifier_key) BETWEEN 1 AND {MAX_RECORD_IDENTIFIER_BYTES}",
            name="ck_version_records_key",
        ),
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identifier_key: Mapped[bytes] = mapped_column(LargeBinary(MAX_RECORD_IDENTIFIER_BYTES))


class DatasetVersionValue(Base):
    __tablename__ = "dataset_version_values"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "record_id"],
            ["dataset_version_records.version_id", "dataset_version_records.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["version_id", "field_id"],
            ["dataset_version_fields.version_id", "dataset_version_fields.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("version_id", "field_id", "unique_key", name="uq_version_values_unique"),
        CheckConstraint("length(canonical_key) BETWEEN 1 AND 1010", name="ck_version_values_key"),
    )
    version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    field_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    canonical_key: Mapped[bytes] = mapped_column(LargeBinary(1010))
    canonical_text: Mapped[str] = mapped_column(Text)
    unique_key: Mapped[bytes | None] = mapped_column(LargeBinary(1010))


class DatasetVersionHierarchyNode(Base):
    __tablename__ = "dataset_version_hierarchy_nodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "parent_id"],
            ["dataset_version_hierarchy_nodes.version_id", "dataset_version_hierarchy_nodes.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
            name="fk_version_hierarchy_nodes_parent",
        ),
        UniqueConstraint(
            "version_id", "parent_id", "name", name="uq_version_hierarchy_nodes_sibling"
        ),
        Index(
            "uq_version_hierarchy_nodes_root",
            "version_id",
            unique=True,
            sqlite_where=text("parent_id IS NULL"),
            postgresql_where=text("parent_id IS NULL"),
        ),
        CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_version_hierarchy_nodes_name"),
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="ck_version_hierarchy_nodes_parent"
        ),
    )

    version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class DatasetVersionHierarchyValue(Base):
    __tablename__ = "dataset_version_hierarchy_values"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "node_id"],
            ["dataset_version_hierarchy_nodes.version_id", "dataset_version_hierarchy_nodes.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["version_id", "field_id"],
            ["dataset_version_fields.version_id", "dataset_version_fields.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "length(canonical_key) BETWEEN 1 AND 1010", name="ck_version_hierarchy_values_key"
        ),
    )

    version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    field_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    canonical_key: Mapped[bytes] = mapped_column(LargeBinary(1010))
    canonical_text: Mapped[str] = mapped_column(Text)


def immutable_version_row(_mapper, _connection, _row):
    raise PersistenceConflictError()


# Lifecycle cancellation is a narrow conditional SQL operation in the adapter.
# ORM callers cannot edit metadata/content or delete individual version rows.
for _snapshot_model in (
    DatasetVersion,
    DatasetVersionField,
    DatasetVersionRecord,
    DatasetVersionValue,
    DatasetVersionHierarchyNode,
    DatasetVersionHierarchyValue,
):
    event.listen(_snapshot_model, "before_update", immutable_version_row)
    event.listen(_snapshot_model, "before_delete", immutable_version_row)


class DatasetConcept(Base):
    __tablename__ = "dataset_concepts"
    __table_args__ = (
        UniqueConstraint("id", name="uq_dataset_concepts_id"),
        CheckConstraint("start_revision >= 1", name="ck_dataset_concepts_revision"),
        ForeignKeyConstraint(
            ["dataset_id", "base_version_id"],
            ["dataset_versions.dataset_id", "dataset_versions.id"],
            ondelete="CASCADE",
            name="fk_concepts_baseline",
        ),
    )
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(36), default=new_id)
    base_version_id: Mapped[str | None] = mapped_column(String(36))
    creator_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    start_revision: Mapped[int] = mapped_column(Integer)
    records_mutated: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, default=False, server_default=false()
    )
    import_result: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    source_update_notes: Mapped[list[str] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )

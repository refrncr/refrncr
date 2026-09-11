from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    false,
    inspect,
    select,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, column_property, mapped_column

from refrncr.datatypes import DATASET_FIELD_TYPES
from refrncr.db.base import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class Organisation(Base):
    __tablename__ = "organisations"
    __table_args__ = (
        CheckConstraint("id = 'community'", name="ck_organisations_single_community"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="community")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
        CheckConstraint("NOT is_deleted OR NOT is_active", name="ck_users_deleted_inactive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_timestamp_id", "timestamp", "id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    details: Mapped[dict[str, str | int] | None] = mapped_column(JSON)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    object_type: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[str] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(100))


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        CheckConstraint("length(name) BETWEEN 1 AND 100", name="ck_teams_name_length"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100))
    _name_key: Mapped[str] = mapped_column("name_key", String(300), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    @hybrid_property
    def name_key(self) -> str:
        return self._name_key

    @name_key.inplace.expression
    @classmethod
    def _name_key_expression(cls):
        return cls._name_key


@event.listens_for(Team, "before_insert")
@event.listens_for(Team, "before_update")
def _derive_team_name_key(_mapper, _connection, team: Team) -> None:
    team._name_key = team.name.casefold()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "team_id", name="uq_memberships_user_team"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Grant(Base):
    __tablename__ = "grants"
    __table_args__ = (
        CheckConstraint("subject_type IN ('user', 'team')", name="ck_grants_subject_type"),
        CheckConstraint("permission IN ('viewer', 'editor', 'admin')", name="ck_grants_permission"),
        UniqueConstraint(
            "subject_type",
            "subject_id",
            "resource_type",
            "resource_id",
            name="uq_grants_subject_resource",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    subject_type: Mapped[str] = mapped_column(String(16), index=True)
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    resource_type: Mapped[str] = mapped_column(String(100), index=True)
    resource_id: Mapped[str] = mapped_column(String(100), index=True)
    permission: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ImmutableDatasetKeyError(ValueError):
    """A persisted Dataset key was changed."""


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        CheckConstraint("length(key) BETWEEN 1 AND 100", name="ck_datasets_key_length"),
        CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_datasets_name_length"),
        CheckConstraint("length(description) <= 10000", name="ck_datasets_description_length"),
        UniqueConstraint("key", name="uq_datasets_key"),
        CheckConstraint("revision >= 1", name="ck_datasets_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    _key: Mapped[str] = mapped_column("key", String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    # owner_user_id is retained as historical creator metadata, never authority.
    owner_team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    owner_team_name = column_property(
        select(Team.name).where(Team.id == owner_team_id).scalar_subquery()
    )

    content_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    @hybrid_property
    def key(self) -> str:
        return self._key

    @key.inplace.expression
    @classmethod
    def _key_expression(cls):
        return cls._key


@event.listens_for(Dataset, "before_update")
def _prevent_dataset_key_update(_mapper, _connection, dataset: Dataset) -> None:
    if inspect(dataset).attrs["_key"].history.has_changes():
        raise ImmutableDatasetKeyError("Dataset key is immutable")


class ImmutableDatasetFieldKeyError(ValueError):
    """A persisted Dataset field key was changed."""


class DatasetField(Base):
    __tablename__ = "dataset_fields"
    __table_args__ = (
        CheckConstraint("length(key) BETWEEN 1 AND 100", name="ck_dataset_fields_key_length"),
        CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_dataset_fields_name_length"),
        CheckConstraint(
            "data_type IN (" + ", ".join(repr(value) for value in DATASET_FIELD_TYPES) + ")",
            name="ck_dataset_fields_data_type",
        ),
        UniqueConstraint("dataset_id", "key", name="uq_dataset_fields_dataset_key"),
        UniqueConstraint("id", "dataset_id", name="uq_dataset_fields_id_dataset"),
        UniqueConstraint(
            "dataset_id", "display_position", name="uq_dataset_fields_dataset_position"
        ),
        CheckConstraint(
            "display_position >= 1", name="ck_dataset_fields_display_position_positive"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    _key: Mapped[str] = mapped_column("key", String(100))
    name: Mapped[str] = mapped_column(String(200))
    data_type: Mapped[str] = mapped_column(String(20))
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_unique: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    display_position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    @hybrid_property
    def key(self) -> str:
        return self._key

    @key.inplace.expression
    @classmethod
    def _key_expression(cls):
        return cls._key


@event.listens_for(DatasetField, "before_update")
def _prevent_dataset_field_key_update(_mapper, _connection, field: DatasetField) -> None:
    if inspect(field).attrs["_key"].history.has_changes():
        raise ImmutableDatasetFieldKeyError("Dataset field key is immutable")


class DatasetBusinessKeyField(Base):
    __tablename__ = "dataset_business_key_fields"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "field_key"],
            ["dataset_fields.dataset_id", "dataset_fields.key"],
            ondelete="CASCADE",
        ),
    )

    dataset_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    field_key: Mapped[str] = mapped_column(String(100), primary_key=True)


class DatasetRecord(Base):
    __tablename__ = "dataset_records"
    __table_args__ = (
        UniqueConstraint("id", "dataset_id", name="uq_dataset_records_id_dataset"),
        UniqueConstraint(
            "dataset_id", "identifier_key", name="uq_dataset_records_dataset_identifier"
        ),
        CheckConstraint("revision >= 1", name="ck_dataset_records_revision_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    identifier_key: Mapped[bytes] = mapped_column(LargeBinary(2000))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DatasetRecordValue(Base):
    __tablename__ = "dataset_record_values"
    __table_args__ = (
        ForeignKeyConstraint(
            ["record_id", "dataset_id"],
            ["dataset_records.id", "dataset_records.dataset_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["field_id", "dataset_id"],
            ["dataset_fields.id", "dataset_fields.dataset_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(CASE WHEN string_value IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN number_value IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN boolean_value IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN date_value IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_dataset_record_values_exactly_one_typed_value",
        ),
        UniqueConstraint("record_id", "field_id", name="uq_dataset_record_values_record_field"),
        UniqueConstraint(
            "dataset_id",
            "field_id",
            "unique_value_key",
            name="uq_dataset_record_values_unique_value",
        ),
    )

    record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    field_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(36))
    string_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    number_value: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    date_value: Mapped[date | None] = mapped_column(Date, nullable=True)
    canonical_value_key: Mapped[bytes] = mapped_column(LargeBinary(1010))
    unique_value_key: Mapped[bytes | None] = mapped_column(LargeBinary(1010), nullable=True)


class DatasetExportAccess(Base):
    __tablename__ = "dataset_export_access"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL AND team_id IS NULL) OR "
            "(user_id IS NULL AND team_id IS NOT NULL)",
            name="ck_dataset_export_access_subject",
        ),
        UniqueConstraint("dataset_id", "user_id", name="uq_dataset_export_access_user"),
        UniqueConstraint("dataset_id", "team_id", name="uq_dataset_export_access_team"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    team_id: Mapped[str | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    @property
    def subject_type(self) -> str:
        return "user" if self.user_id is not None else "team"

    @property
    def subject_id(self) -> str:
        return self.user_id if self.user_id is not None else self.team_id


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "number", name="uq_dataset_versions_number"),
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
    number: Mapped[int] = mapped_column(Integer)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
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
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelling_actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )


class DatasetVersionField(Base):
    __tablename__ = "dataset_version_fields"
    __table_args__ = (
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
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    data_type: Mapped[str] = mapped_column(String(20))
    is_required: Mapped[bool] = mapped_column(Boolean)
    is_unique: Mapped[bool] = mapped_column(Boolean)
    display_position: Mapped[int] = mapped_column(Integer)
    is_identifier: Mapped[bool] = mapped_column(Boolean)


class DatasetVersionRecord(Base):
    __tablename__ = "dataset_version_records"
    __table_args__ = (
        UniqueConstraint("version_id", "identifier_key", name="uq_version_records_identifier"),
        CheckConstraint("length(identifier_key) BETWEEN 1 AND 2000", name="ck_version_records_key"),
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identifier_key: Mapped[bytes] = mapped_column(LargeBinary(2000))


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


def _immutable_version_row(_mapper, _connection, _row):
    from refrncr.ports import PersistenceConflictError

    raise PersistenceConflictError()


# Lifecycle cancellation is a narrow conditional SQL operation in the adapter.
# ORM callers cannot edit metadata/content or delete individual version rows.
for _snapshot_model in (
    DatasetVersion,
    DatasetVersionField,
    DatasetVersionRecord,
    DatasetVersionValue,
):
    event.listen(_snapshot_model, "before_update", _immutable_version_row)
    event.listen(_snapshot_model, "before_delete", _immutable_version_row)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    start_revision: Mapped[int] = mapped_column(Integer)

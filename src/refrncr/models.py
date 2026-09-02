from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column

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

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
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
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    _key: Mapped[str] = mapped_column("key", String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
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


@event.listens_for(Dataset, "before_update")
def _prevent_dataset_key_update(_mapper, _connection, dataset: Dataset) -> None:
    if inspect(dataset).attrs["_key"].history.has_changes():
        raise ImmutableDatasetKeyError("Dataset key is immutable")

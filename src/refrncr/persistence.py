from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict
from functools import wraps
from types import SimpleNamespace, TracebackType

from sqlalchemy import (
    Text,
    and_,
    case,
    cast,
    delete,
    func,
    inspect,
    or_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from refrncr.api_key_persistence import SqlAlchemyApiKeyOperations
from refrncr.datatypes import DatatypeError, get_datatype
from refrncr.models import (
    AuditEvent,
    Dataset,
    DatasetBusinessKeyField,
    DatasetExportAccess,
    DatasetField,
    DatasetFieldSourceFormat,
    DatasetRecord,
    DatasetRecordValue,
    DatasetVersion,
    Grant,
    LoginSession,
    Membership,
    Organisation,
    Team,
    User,
    new_id,
    utc_now,
)
from refrncr.ports import (
    AuditObjectRecord,
    AuditReadUnavailable,
    AuthorizationSubject,
    DatasetBusinessKey,
    DatasetFieldRecord,
    DatasetOverview,
    GrantRecord,
    MembershipRecord,
    MembershipRelationship,
    PasswordHashReplacementError,
    PermissionContext,
    PersistenceConflictError,
    RecordValue,
    ResourcePermissionsContext,
    StoredRecord,
    TeamRecord,
)


def _locked(statement, session: Session, for_update: bool):
    if for_update and session.get_bind().dialect.name != "sqlite":
        return statement.with_for_update().execution_options(populate_existing=True)
    return statement


def _safe_constraint_name(error: IntegrityError) -> str | None:
    """Return only known canonical constraint identifiers, never driver detail."""
    diagnostic = getattr(error.orig, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == "uq_datasets_key":
        return "uq_datasets_key"
    if getattr(diagnostic, "constraint_name", None) == "uq_dataset_fields_dataset_key":
        return "uq_dataset_fields_dataset_key"
    if getattr(diagnostic, "constraint_name", None) == "uq_dataset_fields_dataset_position":
        return "uq_dataset_fields_dataset_position"
    if getattr(diagnostic, "constraint_name", None) in {
        "uq_dataset_export_access_user",
        "uq_dataset_export_access_team",
        "uq_dataset_records_dataset_identifier",
        "uq_dataset_record_values_unique_value",
    }:
        return diagnostic.constraint_name
    if str(error.orig) == "UNIQUE constraint failed: datasets.key":
        return "uq_datasets_key"
    if str(error.orig) == (
        "UNIQUE constraint failed: dataset_fields.dataset_id, dataset_fields.key"
    ):
        return "uq_dataset_fields_dataset_key"
    if str(error.orig) == (
        "UNIQUE constraint failed: dataset_fields.dataset_id, dataset_fields.display_position"
    ):
        return "uq_dataset_fields_dataset_position"
    if str(error.orig) == (
        "UNIQUE constraint failed: dataset_records.dataset_id, dataset_records.identifier_key"
    ):
        return "uq_dataset_records_dataset_identifier"
    if str(error.orig) == (
        "UNIQUE constraint failed: dataset_record_values.dataset_id, "
        "dataset_record_values.field_id, dataset_record_values.unique_value_key"
    ):
        return "uq_dataset_record_values_unique_value"
    for subject in ("user", "team"):
        if str(error.orig) == (
            "UNIQUE constraint failed: dataset_export_access.dataset_id, "
            f"dataset_export_access.{subject}_id"
        ):
            return f"uq_dataset_export_access_{subject}"
    return None


class SqlAlchemyUserOperations:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, user_id: str, *, for_update: bool = False) -> User | None:
        statement = _locked(select(User).where(User.id == user_id), self.session, for_update)
        return self.session.scalar(statement)

    def find_by_email(self, email: str) -> User | None:
        return self.session.scalar(select(User).where(User.email == email))

    def list_by_email(self) -> list[User]:
        return list(self.session.scalars(select(User).order_by(User.email)))

    def count(self) -> int:
        return self.session.scalar(select(func.count(User.id))) or 0

    def count_active_admins(self) -> int:
        return (
            self.session.scalar(
                select(func.count(User.id)).where(
                    User.role == "admin",
                    User.is_active.is_(True),
                    User.is_deleted.is_(False),
                )
            )
            or 0
        )

    def create(self, **values) -> User:
        user = User(**values)
        self.session.add(user)
        return user

    def replace_password_hash(
        self, user_id: str, expected_hash: str, replacement_hash: str
    ) -> bool:
        self.session.info["password_hash_replacement_attempted"] = True
        try:
            result = self.session.execute(
                update(User)
                .where(User.id == user_id, User.password_hash == expected_hash)
                .values(password_hash=replacement_hash, updated_at=utc_now())
            )
            self.session.flush()
        except SQLAlchemyError:
            raise PasswordHashReplacementError("Password hash replacement failed") from None
        return result.rowcount == 1

    def revoke_active_sessions(self, user_id: str, revoked_at) -> None:
        self.session.execute(
            update(LoginSession)
            .where(LoginSession.user_id == user_id, LoginSession.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )

    def get_community_organisation(self) -> Organisation | None:
        return self.session.get(Organisation, "community")

    def create_community_organisation(self) -> Organisation:
        organisation = Organisation()
        self.session.add(organisation)
        return organisation

    def lock_community_setup(self) -> Organisation | None:
        statement = _locked(
            select(Organisation).where(Organisation.id == "community"), self.session, True
        )
        return self.session.scalar(statement)


class SqlAlchemyTeamOperations:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, team_id: str, *, for_update: bool = False) -> Team | None:
        statement = _locked(select(Team).where(Team.id == team_id), self.session, for_update)
        return self.session.scalar(statement)

    def list_by_name(self) -> list[Team]:
        return list(self.session.scalars(select(Team).order_by(Team.name_key)))

    def create(self, *, name: str) -> Team:
        team = Team(name=name)
        self.session.add(team)
        return team

    def delete(self, team: TeamRecord) -> None:
        self.session.delete(team)


class SqlAlchemyMembershipOperations:
    def list_relationships(self):
        return tuple(
            MembershipRelationship(*row)
            for row in self.session.execute(
                select(User.id, User.display_name, Team.id, Team.name)
                .select_from(Membership)
                .join(User, User.id == Membership.user_id)
                .join(Team, Team.id == Membership.team_id)
                .where(User.is_deleted.is_(False))
            )
        )

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, membership_id: str, *, for_update: bool = False) -> Membership | None:
        statement = _locked(
            select(Membership).where(Membership.id == membership_id), self.session, for_update
        )
        return self.session.scalar(statement)

    def create(self, *, user_id: str, team_id: str) -> Membership:
        membership = Membership(user_id=user_id, team_id=team_id)
        self.session.add(membership)
        return membership

    def delete(self, membership: MembershipRecord) -> None:
        self.session.delete(membership)

    def team_ids_for_user(self, user_id: str) -> tuple[str, ...]:
        return tuple(
            self.session.scalars(
                select(Membership.team_id)
                .join(Team, Team.id == Membership.team_id)
                .where(Membership.user_id == user_id)
                .order_by(Membership.team_id)
            )
        )

    def list_team_members(self, team_id: str) -> list[tuple[Membership, User]]:
        statement = (
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.team_id == team_id)
            .order_by(User.email)
        )
        return list(self.session.execute(statement).tuples())

    def list_eligible_users(self, team_id: str) -> list[User]:
        existing = select(Membership.user_id).where(Membership.team_id == team_id)
        statement = (
            select(User)
            .where(
                User.is_active.is_(True),
                User.is_deleted.is_(False),
                User.id.not_in(existing),
            )
            .order_by(User.email)
        )
        return list(self.session.scalars(statement))


class SqlAlchemyGrantOperations:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, grant_id: str, *, for_update: bool = False) -> Grant | None:
        statement = _locked(select(Grant).where(Grant.id == grant_id), self.session, for_update)
        return self.session.scalar(statement)

    def create(self, **values) -> Grant:
        grant = Grant(**values)
        self.session.add(grant)
        return grant

    def delete(self, grant: GrantRecord) -> None:
        self.session.delete(grant)

    def list_for_team(self, team_id: str, *, for_update: bool = False) -> list[Grant]:
        statement = (
            select(Grant)
            .where(Grant.subject_type == "team", Grant.subject_id == team_id)
            .order_by(Grant.id)
        )
        statement = _locked(statement, self.session, for_update)
        return list(self.session.scalars(statement))


class SqlAlchemyDatasetOperations:
    def deletion_counts(self, dataset_id: str) -> tuple[int, int, int, int]:
        def total(model):
            return self.session.scalar(
                select(func.count()).select_from(model).where(model.dataset_id == dataset_id)
            )

        return (
            total(DatasetField),
            total(DatasetRecord),
            total(DatasetVersion),
            total(DatasetExportAccess),
        )

    def delete_with_dependents(self, dataset_id: str) -> None:
        # REF-141 inventory: values RESTRICT Field deletion. Do not rely on
        # unspecified ordering of multiple database cascades from Dataset.
        for model in (
            DatasetVersion,
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
            .values(owner_team_id=owner_team_id, updated_at=utc_now())
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
            raise PersistenceConflictError(_safe_constraint_name(exc)) from None
        if result.rowcount != 1:
            raise PersistenceConflictError()

    def current_overview(self, dataset_ids: tuple[str, ...] | None) -> list[DatasetOverview]:
        if dataset_ids == ():
            return []
        # DatasetRecord contains only the mutable working set. Future version storage
        # must preserve this boundary, never union historical/published rows here.
        current_counts = select(
            DatasetRecord.dataset_id, func.count(DatasetRecord.id).label("record_count")
        ).where(DatasetRecord.deleted_content.is_(None))
        if dataset_ids is not None:
            current_counts = current_counts.where(DatasetRecord.dataset_id.in_(dataset_ids))
        current_counts = current_counts.group_by(DatasetRecord.dataset_id).subquery()
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
        statement = _locked(
            select(Dataset).where(Dataset.id == dataset_id), self.session, for_update
        )
        return self.session.scalar(statement)

    def find_by_key(self, key: str, *, for_update: bool = False) -> Dataset | None:
        statement = _locked(select(Dataset).where(Dataset._key == key), self.session, for_update)
        return self.session.scalar(statement)

    def list_all(self) -> list[Dataset]:
        return list(self.session.scalars(select(Dataset).order_by(Dataset._key)))

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
        statement = _locked(
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
            from sqlalchemy import MetaData, Table

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

    def create(self, **values) -> DatasetField:
        if not self._has_display_position():
            values.pop("display_position", None)
            values["id"] = new_id()
            values["created_at"] = values["updated_at"] = utc_now()
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
        keys = self.session.scalars(
            select(DatasetBusinessKeyField.field_key)
            .where(DatasetBusinessKeyField.dataset_id == dataset_id)
            .order_by(DatasetBusinessKeyField.field_key)
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


class SqlAlchemyDatasetRecordOperations:
    def __init__(self, session: Session, *, legacy_schema=False) -> None:
        self.session = session
        self._legacy_schema = legacy_schema
        self._active_records = True if legacy_schema else DatasetRecord.deleted_content.is_(None)

    def _available(self) -> bool:
        return inspect(self.session.get_bind()).has_table("dataset_records")

    def identifier_keys(self, dataset_id):
        return dict(
            self.session.execute(
                select(DatasetRecord.id, DatasetRecord.identifier_key).where(
                    DatasetRecord.dataset_id == dataset_id, self._active_records
                )
            ).all()
        )

    def create(self, *, dataset_id: str, identifier_key: bytes) -> DatasetRecord:
        record = DatasetRecord(dataset_id=dataset_id, identifier_key=identifier_key, revision=1)
        self.session.add(record)
        return record

    def get(self, dataset_id: str, record_id: str) -> StoredRecord | None:
        record = self.session.scalar(
            select(DatasetRecord).where(
                DatasetRecord.dataset_id == dataset_id,
                self._active_records,
                DatasetRecord.id == record_id,
            )
        )
        if record is None:
            return None
        return StoredRecord(
            record.id, record.dataset_id, self._values(dataset_id, record_id), record.revision
        )

    def update_if_revision(
        self,
        dataset_id: str,
        record_id: str,
        expected_revision: int,
        identifier_key: bytes,
    ) -> bool:
        try:
            result = self.session.execute(
                update(DatasetRecord)
                .where(
                    DatasetRecord.dataset_id == dataset_id,
                    self._active_records,
                    DatasetRecord.id == record_id,
                    DatasetRecord.revision == expected_revision,
                )
                .values(
                    identifier_key=identifier_key,
                    revision=DatasetRecord.revision + 1,
                    updated_at=utc_now(),
                )
            )
        except IntegrityError as exc:
            raise PersistenceConflictError(_safe_constraint_name(exc)) from None
        return result.rowcount == 1

    def replace_values(self, record: DatasetRecord, values: tuple[RecordValue, ...]) -> None:
        self.session.execute(
            delete(DatasetRecordValue).where(
                DatasetRecordValue.dataset_id == record.dataset_id,
                DatasetRecordValue.record_id == record.id,
            )
        )
        self.add_values(record, values)

    def delete_if_revision(self, dataset_id: str, record_id: str, expected_revision: int) -> bool:
        from refrncr.version_contracts import SnapshotField

        record = self.get(dataset_id, record_id)
        if record is None or record.revision != expected_revision:
            return False
        keys = SqlAlchemyDatasetBusinessKeyOperations(self.session).get(dataset_id).field_keys
        fields = SqlAlchemyDatasetFieldOperations(self.session).list_for_dataset(dataset_id)
        payload = {
            "fields": [
                asdict(
                    SnapshotField(
                        f.id,
                        dataset_id,
                        f.key,
                        f.name,
                        f.data_type,
                        f.is_required,
                        f.is_unique,
                        f.display_position,
                        f.key in keys,
                        f.definition,
                        f.unit,
                    )
                )
                for f in fields
            ],
            "values": {v.field_id: v.canonical_key.hex() for v in record.values},
        }
        result = self.session.execute(
            update(DatasetRecord)
            .where(
                DatasetRecord.dataset_id == dataset_id,
                DatasetRecord.id == record_id,
                DatasetRecord.revision == expected_revision,
                self._active_records,
            )
            .values(deleted_content=payload, revision=expected_revision + 1, updated_at=utc_now())
        )
        if result.rowcount:
            self.session.execute(
                delete(DatasetRecordValue).where(
                    DatasetRecordValue.dataset_id == dataset_id,
                    DatasetRecordValue.record_id == record_id,
                )
            )
        return result.rowcount == 1

    def deleted_records(self, dataset_id):
        from refrncr.version_contracts import SnapshotField

        statement = (
            select(DatasetRecord)
            .where(
                DatasetRecord.dataset_id == dataset_id, DatasetRecord.deleted_content.is_not(None)
            )
            .order_by(DatasetRecord.id)
            .execution_options(yield_per=100)
        )
        for row in self.session.scalars(statement):
            fields = tuple(SnapshotField(**f) for f in row.deleted_content["fields"])
            values = []
            for field in fields:
                encoded = row.deleted_content["values"].get(field.id)
                if encoded is not None:
                    key = bytes.fromhex(encoded)
                    values.append(
                        RecordValue(
                            field.id,
                            field.key,
                            field.data_type,
                            get_datatype(field.data_type).from_key(key),
                            key,
                            field.is_unique,
                        )
                    )
            yield StoredRecord(row.id, dataset_id, tuple(values), row.revision), fields

    def restore_if_revision(self, dataset_id, record_id, expected_revision, identifier_key):
        try:
            result = self.session.execute(
                update(DatasetRecord)
                .where(
                    DatasetRecord.dataset_id == dataset_id,
                    DatasetRecord.id == record_id,
                    DatasetRecord.revision == expected_revision,
                    DatasetRecord.deleted_content.is_not(None),
                )
                .values(
                    deleted_content=None,
                    identifier_key=identifier_key,
                    revision=expected_revision + 1,
                    updated_at=utc_now(),
                )
            )
        except IntegrityError as exc:
            raise PersistenceConflictError(_safe_constraint_name(exc)) from None
        return result.rowcount == 1

    def current_revision(self, dataset_id: str, record_id: str) -> int | None:
        return self.session.scalar(
            select(DatasetRecord.revision).where(
                DatasetRecord.dataset_id == dataset_id,
                self._active_records,
                DatasetRecord.id == record_id,
            )
        )

    def add_values(self, record: DatasetRecord, values: tuple[RecordValue, ...]) -> None:
        rows = []
        for value in values:
            typed = {
                "string_value": None,
                "number_value": None,
                "boolean_value": None,
                "date_value": None,
            }
            datatype = get_datatype(value.data_type)
            decoded = datatype.from_key(value.canonical_key)
            if type(value.value) is not datatype.python_type or decoded != value.value:
                raise DatatypeError("Invalid stored canonical value", "invalid_storage")
            if value.data_type in {"string", "number", "boolean", "date"}:
                typed[f"{value.data_type}_value"] = value.value
            else:
                typed["string_value"] = datatype.text(decoded)
            rows.append(
                DatasetRecordValue(
                    record_id=record.id,
                    dataset_id=record.dataset_id,
                    field_id=value.field_id,
                    canonical_value_key=value.canonical_key,
                    unique_value_key=value.canonical_key if value.is_unique else None,
                    **typed,
                )
            )
        self.session.add_all(rows)

    def list_all(self, dataset_id: str) -> list[StoredRecord]:
        records = list(
            self.session.scalars(
                select(DatasetRecord)
                .where(DatasetRecord.dataset_id == dataset_id, self._active_records)
                .order_by(DatasetRecord.id)
            )
        )
        values = self.session.execute(
            select(DatasetRecordValue, DatasetField)
            .join(
                DatasetField,
                (DatasetField.id == DatasetRecordValue.field_id)
                & (DatasetField.dataset_id == DatasetRecordValue.dataset_id),
            )
            .where(DatasetRecordValue.dataset_id == dataset_id)
        )
        grouped: dict[str, list[RecordValue]] = {record.id: [] for record in records}
        for row, field in values:
            grouped[row.record_id].append(self._record_value(row, field))
        return [
            StoredRecord(record.id, record.dataset_id, tuple(grouped[record.id]), record.revision)
            for record in records
        ]

    def iterate_all(self, dataset_id: str) -> Iterator[StoredRecord]:
        statement = (
            select(DatasetRecord, DatasetRecordValue, DatasetField)
            .outerjoin(
                DatasetRecordValue,
                (DatasetRecordValue.record_id == DatasetRecord.id)
                & (DatasetRecordValue.dataset_id == DatasetRecord.dataset_id),
            )
            .outerjoin(
                DatasetField,
                (DatasetField.id == DatasetRecordValue.field_id)
                & (DatasetField.dataset_id == DatasetRecordValue.dataset_id),
            )
            .where(DatasetRecord.dataset_id == dataset_id, self._active_records)
            .order_by(DatasetRecord.id, DatasetRecordValue.field_id)
            .execution_options(yield_per=1000)
        )
        if self._legacy_schema:
            from sqlalchemy.orm import defer

            statement = statement.options(
                defer(DatasetRecord.deleted_content),
                defer(DatasetField.definition),
                defer(DatasetField.unit),
            )
        current = None
        values: list[RecordValue] = []
        for record, row, field in self.session.execute(statement):
            if current is not None and record.id != current.id:
                yield StoredRecord(current.id, current.dataset_id, tuple(values), current.revision)
                values = []
            current = record
            if row is not None and field is not None:
                values.append(self._record_value(row, field))
        if current is not None:
            yield StoredRecord(current.id, current.dataset_id, tuple(values), current.revision)

    @staticmethod
    def _record_value(row: DatasetRecordValue, field: DatasetField) -> RecordValue:
        datatype = get_datatype(field.data_type)
        value = datatype.from_key(row.canonical_value_key)
        columns = {
            "string": row.string_value,
            "number": row.number_value,
            "boolean": row.boolean_value,
            "date": row.date_value,
        }
        column = field.data_type if field.data_type in columns else "string"
        if sum(item is not None for item in columns.values()) != 1 or columns[column] is None:
            raise DatatypeError("Invalid stored canonical value", "invalid_storage")
        # Numeric is an approximate SQLite projection; the exact key is authoritative.
        expected = value if field.data_type in columns else datatype.text(value)
        if field.data_type != "number" and columns[column] != expected:
            raise DatatypeError("Invalid stored canonical value", "invalid_storage")
        return RecordValue(
            field.id,
            field.key,
            field.data_type,
            value,
            row.canonical_value_key,
            field.is_unique,
        )

    def existing_identifier_keys(
        self, dataset_id: str, identifier_keys: tuple[bytes, ...]
    ) -> set[bytes]:
        found: set[bytes] = set()
        for start in range(0, len(identifier_keys), 500):
            chunk = identifier_keys[start : start + 500]
            found.update(
                self.session.scalars(
                    select(DatasetRecord.identifier_key).where(
                        DatasetRecord.dataset_id == dataset_id,
                        self._active_records,
                        DatasetRecord.identifier_key.in_(chunk),
                    )
                )
            )
        return found

    def existing_unique_value_keys(
        self, dataset_id: str, values: tuple[tuple[str, bytes], ...]
    ) -> set[tuple[str, bytes]]:
        found: set[tuple[str, bytes]] = set()
        for start in range(0, len(values), 250):
            chunk = values[start : start + 250]
            if not chunk:
                continue
            rows = self.session.execute(
                select(
                    DatasetRecordValue.field_id,
                    DatasetRecordValue.unique_value_key,
                ).where(
                    DatasetRecordValue.dataset_id == dataset_id,
                    or_(
                        *(
                            and_(
                                DatasetRecordValue.field_id == field_id,
                                DatasetRecordValue.unique_value_key == key,
                            )
                            for field_id, key in chunk
                        )
                    ),
                )
            )
            found.update((field_id, key) for field_id, key in rows if key is not None)
        return found

    def _values(self, dataset_id: str, record_id: str) -> tuple[RecordValue, ...]:
        rows = self.session.execute(
            select(DatasetRecordValue, DatasetField)
            .join(
                DatasetField,
                (DatasetField.id == DatasetRecordValue.field_id)
                & (DatasetField.dataset_id == DatasetRecordValue.dataset_id),
            )
            .where(
                DatasetRecordValue.dataset_id == dataset_id,
                DatasetRecordValue.record_id == record_id,
            )
        )
        return tuple(self._record_value(row, field) for row, field in rows)

    def count(self, dataset_id: str) -> int:
        if not self._available():
            return 0
        return (
            self.session.scalar(
                select(func.count(DatasetRecord.id)).where(
                    DatasetRecord.dataset_id == dataset_id, self._active_records
                )
            )
            or 0
        )

    def field_has_values(self, dataset_id: str, field_id: str) -> bool:
        if not self._available():
            return False
        return (
            self.session.scalar(
                select(DatasetRecordValue.record_id)
                .where(
                    DatasetRecordValue.dataset_id == dataset_id,
                    DatasetRecordValue.field_id == field_id,
                )
                .limit(1)
            )
            is not None
        )

    def field_has_missing_values(self, dataset_id: str, field_id: str) -> bool:
        if not self._available():
            return False
        value_records = select(DatasetRecordValue.record_id).where(
            DatasetRecordValue.dataset_id == dataset_id,
            DatasetRecordValue.field_id == field_id,
        )
        return (
            self.session.scalar(
                select(DatasetRecord.id)
                .where(
                    DatasetRecord.dataset_id == dataset_id,
                    self._active_records,
                    DatasetRecord.id.not_in(value_records),
                )
                .limit(1)
            )
            is not None
        )

    def field_has_duplicate_values(self, dataset_id: str, field_id: str) -> bool:
        if not self._available():
            return False
        return (
            self.session.scalar(
                select(DatasetRecordValue.canonical_value_key)
                .where(
                    DatasetRecordValue.dataset_id == dataset_id,
                    DatasetRecordValue.field_id == field_id,
                )
                .group_by(DatasetRecordValue.canonical_value_key)
                .having(func.count() > 1)
                .limit(1)
            )
            is not None
        )

    def set_field_unique_keys(self, dataset_id: str, field_id: str, enabled: bool) -> None:
        if not self._available():
            return
        self.session.execute(
            update(DatasetRecordValue)
            .where(
                DatasetRecordValue.dataset_id == dataset_id,
                DatasetRecordValue.field_id == field_id,
            )
            .values(unique_value_key=(DatasetRecordValue.canonical_value_key if enabled else None))
        )


def _audit_read(method):
    @wraps(method)
    def read(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except SQLAlchemyError:
            raise AuditReadUnavailable("Audit history is temporarily unavailable") from None

    return read


class SqlAlchemyAuditReadOperations:
    """Bounded projection reads. No relationships, business content or write methods."""

    def __init__(self, session):
        self.session = session

    @staticmethod
    def _statement(*, detail=False):
        # Never load unbounded historical JSON. Lists only need the REF-141
        # deletion snapshot; details retain the same 4096-character ceiling.
        context = cast(AuditEvent.details, Text)
        details = (
            case((func.length(context) <= 4096, context), else_=None)
            if detail
            else case(
                (
                    (AuditEvent.object_type == "dataset")
                    & (
                        AuditEvent.action.in_(
                            (
                                "dataset.deleted",
                                "dataset.version_published",
                                "dataset.version_cancelled",
                            )
                        )
                    )
                    & (func.length(context) <= 4096),
                    context,
                ),
                else_=None,
            )
        )
        return select(
            AuditEvent.id,
            AuditEvent.timestamp,
            AuditEvent.actor_user_id,
            AuditEvent.object_type,
            AuditEvent.object_id,
            AuditEvent.action,
            details.label("details"),
        )

    @staticmethod
    def _record(row):
        values = dict(row._mapping)
        try:
            values["details"] = json.loads(values["details"]) if values["details"] else None
        except (ValueError, TypeError, RecursionError):
            values["details"] = None
        return SimpleNamespace(**values)

    @_audit_read
    def page(self, filters, *, before, ceiling, newer=False):
        statement = self._statement()
        if filters.start:
            statement = statement.where(AuditEvent.timestamp >= filters.start)
        if filters.end:
            statement = statement.where(AuditEvent.timestamp < filters.end)
        if filters.actor:
            statement = statement.where(AuditEvent.actor_user_id == filters.actor)
        if filters.object_type:
            statement = statement.where(AuditEvent.object_type == filters.object_type)
        if filters.action:
            kind, action = filters.action.split(":", 1)
            statement = statement.where(AuditEvent.object_type == kind, AuditEvent.action == action)
        ordering = tuple_(AuditEvent.timestamp, AuditEvent.id)
        if ceiling:
            statement = statement.where(ordering <= tuple_(ceiling.timestamp, ceiling.id))
        if before:
            marker = tuple_(before.timestamp, before.id)
            statement = statement.where(ordering > marker if newer else ordering < marker)
        columns = (AuditEvent.timestamp, AuditEvent.id)
        statement = statement.order_by(*(c.asc() if newer else c.desc() for c in columns)).limit(51)
        return tuple(self._record(row) for row in self.session.execute(statement))

    @_audit_read
    def get(self, event_id):
        row = self.session.execute(
            self._statement(detail=True).where(AuditEvent.id == event_id)
        ).first()
        return self._record(row) if row else None

    @_audit_read
    def actor_names(self, ids):
        if len(ids) > 51:
            raise ValueError("Audit actor lookup exceeds one page")
        return (
            dict(
                self.session.execute(
                    select(User.id, User.display_name).where(
                        User.id.in_(ids), User.is_deleted.is_(False)
                    )
                ).all()
            )
            if ids
            else {}
        )

    @_audit_read
    def resolve_objects(self, object_type, ids):
        if len(ids) > 50:
            raise ValueError("Audit object lookup exceeds one page")
        columns = {
            "user": (User.id, User.display_name),
            "team": (Team.id, Team.name),
            "dataset": (Dataset.id, Dataset.name),
            "dataset_field": (DatasetField.id, DatasetField.name),
        }.get(object_type)
        if columns is None:
            raise AuditReadUnavailable("Audit object resolution is unavailable")
        if not ids:
            return {}
        statement = select(*columns).where(columns[0].in_(ids))
        if object_type == "user":
            statement = statement.where(User.is_deleted.is_(False))
        names = dict(self.session.execute(statement).all())
        return {key: AuditObjectRecord(key in names, names.get(key)) for key in ids}


class SqlAlchemyUnitOfWork:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        write: bool = False,
        serialized: bool = False,
        translate_errors: bool = False,
    ) -> None:
        self.sessions = sessions
        self.write = write
        self.serialized = serialized
        self.translate_errors = translate_errors

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self.sessions()
        try:
            if self.write:
                if self.serialized and self.session.get_bind().dialect.name == "sqlite":
                    self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    self.session.begin()
        except SQLAlchemyError:
            self.session.close()
            if self.translate_errors:
                raise PersistenceConflictError() from None
            raise
        self.users = SqlAlchemyUserOperations(self.session)
        self.api_keys = SqlAlchemyApiKeyOperations(self.session)
        self.audit_history = SqlAlchemyAuditReadOperations(self.session)
        self.teams = SqlAlchemyTeamOperations(self.session)
        self.memberships = SqlAlchemyMembershipOperations(self.session)
        self.grants = SqlAlchemyGrantOperations(self.session)
        self.datasets = SqlAlchemyDatasetOperations(self.session)
        self.dataset_export_access = SqlAlchemyDatasetExportAccessOperations(self.session)
        self.dataset_fields = SqlAlchemyDatasetFieldOperations(self.session)
        self.dataset_business_keys = SqlAlchemyDatasetBusinessKeyOperations(self.session)
        self.dataset_records = SqlAlchemyDatasetRecordOperations(self.session)
        from refrncr.version_persistence import SqlAlchemyVersionOperations

        self.versions = SqlAlchemyVersionOperations(self.session)
        from refrncr.diff_persistence import SqlAlchemyDiffOperations

        self.version_diff = SqlAlchemyDiffOperations(self.session)
        from refrncr.concept_persistence import SqlAlchemyConceptOperations

        self.concepts = SqlAlchemyConceptOperations(self.session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            if exc_type is None and self.write:
                try:
                    self.session.commit()
                except SQLAlchemyError:
                    self.session.rollback()
                    if self.session.info.get("password_hash_replacement_attempted"):
                        raise PasswordHashReplacementError(
                            "Password hash replacement failed"
                        ) from None
                    if self.translate_errors:
                        raise PersistenceConflictError() from None
                    raise
                except Exception:
                    self.session.rollback()
                    raise
            elif self.write:
                self.session.rollback()
        finally:
            self.session.close()
        if self.translate_errors and isinstance(exc_value, SQLAlchemyError):
            raise PersistenceConflictError() from None
        return False

    def flush(self) -> None:
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise PersistenceConflictError(_safe_constraint_name(exc)) from None

    def begin_export_snapshot(self) -> None:
        """Start the engine-appropriate read snapshot before any export query."""
        bind = self.session.get_bind()
        if bind.dialect.name == "postgresql":
            self.session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        elif bind.dialect.name == "sqlite":
            # sqlite3 legacy transaction control does not start a transaction for
            # SELECT. Pin authority and content to the same explicit read snapshot.
            self.session.connection().exec_driver_sql("BEGIN")
        else:
            self.session.connection()

    def add_audit(self, **values) -> None:
        self.session.add(AuditEvent(**values))


class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def __call__(
        self, *, write: bool = False, serialized: bool = False, translate_errors: bool = False
    ) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(
            self.sessions, write=write, serialized=serialized, translate_errors=translate_errors
        )


class SqlAlchemyPermissionDataSource:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def load_context(self, user_id: str, resource_type: str, resource_id: str) -> PermissionContext:
        with self.sessions() as session:
            user = session.get(User, user_id)
            if user is None:
                return PermissionContext(AuthorizationSubject(False, False, False, False), ())
            subject = AuthorizationSubject(
                exists=True,
                active=user.is_active,
                deleted=user.is_deleted,
                community_admin=user.role == "admin",
            )
            if not user.is_active or user.is_deleted:
                return PermissionContext(subject, ())
            team_ids = select(Membership.team_id).where(Membership.user_id == user.id)
            permissions = session.scalars(
                select(Grant.permission).where(
                    Grant.resource_type == resource_type,
                    Grant.resource_id == resource_id,
                    (
                        ((Grant.subject_type == "user") & (Grant.subject_id == user.id))
                        | ((Grant.subject_type == "team") & Grant.subject_id.in_(team_ids))
                    ),
                )
            )
            return PermissionContext(subject, tuple(permissions))

    def load_resource_permissions(
        self, user_id: str, resource_type: str
    ) -> ResourcePermissionsContext:
        with self.sessions() as session:
            user = session.get(User, user_id)
            if user is None:
                return ResourcePermissionsContext(
                    AuthorizationSubject(False, False, False, False), ()
                )
            subject = AuthorizationSubject(
                exists=True,
                active=user.is_active,
                deleted=user.is_deleted,
                community_admin=user.role == "admin",
            )
            if not user.is_active or user.is_deleted:
                return ResourcePermissionsContext(subject, ())
            team_ids = select(Membership.team_id).where(Membership.user_id == user.id)
            permissions = session.execute(
                select(Grant.resource_id, Grant.permission).where(
                    Grant.resource_type == resource_type,
                    (
                        ((Grant.subject_type == "user") & (Grant.subject_id == user.id))
                        | ((Grant.subject_type == "team") & Grant.subject_id.in_(team_ids))
                    ),
                )
            )
            return ResourcePermissionsContext(subject, tuple(permissions.tuples()))


class SqlAlchemyDatasetExportAccessOperations:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, access_id):
        return self.session.get(DatasetExportAccess, access_id)

    def list_for_dataset(self, dataset_id):
        return list(
            self.session.scalars(
                select(DatasetExportAccess)
                .where(DatasetExportAccess.dataset_id == dataset_id)
                .order_by(DatasetExportAccess.created_at, DatasetExportAccess.id)
            )
        )

    def applies(self, dataset_id, user_id, team_ids):
        return (
            self.session.scalar(
                select(DatasetExportAccess.id)
                .where(
                    DatasetExportAccess.dataset_id == dataset_id,
                    or_(
                        DatasetExportAccess.user_id == user_id,
                        DatasetExportAccess.team_id.in_(team_ids),
                    ),
                )
                .limit(1)
            )
            is not None
        )

    def create(self, dataset_id, subject_type, subject_id):
        entry = DatasetExportAccess(
            dataset_id=dataset_id,
            user_id=subject_id if subject_type == "user" else None,
            team_id=subject_id if subject_type == "team" else None,
        )
        self.session.add(entry)
        return entry

    def delete(self, access):
        self.session.delete(access)

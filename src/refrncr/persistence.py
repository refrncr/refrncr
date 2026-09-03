from __future__ import annotations

from decimal import Decimal
from types import TracebackType

from sqlalchemy import delete, func, inspect, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from refrncr.models import (
    AuditEvent,
    Dataset,
    DatasetBusinessKeyField,
    DatasetField,
    DatasetRecord,
    DatasetRecordValue,
    Grant,
    LoginSession,
    Membership,
    Organisation,
    Team,
    User,
    utc_now,
)
from refrncr.ports import (
    AuthorizationSubject,
    DatasetBusinessKey,
    DatasetFieldRecord,
    GrantRecord,
    MembershipRecord,
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
        return statement.with_for_update()
    return statement


def _safe_constraint_name(error: IntegrityError) -> str | None:
    """Return only known canonical constraint identifiers, never driver detail."""
    diagnostic = getattr(error.orig, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == "uq_datasets_key":
        return "uq_datasets_key"
    if getattr(diagnostic, "constraint_name", None) == "uq_dataset_fields_dataset_key":
        return "uq_dataset_fields_dataset_key"
    if getattr(diagnostic, "constraint_name", None) in {
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
        "UNIQUE constraint failed: dataset_records.dataset_id, dataset_records.identifier_key"
    ):
        return "uq_dataset_records_dataset_identifier"
    if str(error.orig) == (
        "UNIQUE constraint failed: dataset_record_values.dataset_id, "
        "dataset_record_values.field_id, dataset_record_values.unique_value_key"
    ):
        return "uq_dataset_record_values_unique_value"
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
    def __init__(self, session: Session) -> None:
        self.session = session

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
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_key(
        self, dataset_id: str, key: str, *, for_update: bool = False
    ) -> DatasetField | None:
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
        return list(
            self.session.scalars(
                select(DatasetField)
                .where(DatasetField.dataset_id == dataset_id)
                .order_by(DatasetField._key)
            )
        )

    def create(self, **values) -> DatasetField:
        field = DatasetField(_key=values.pop("key"), **values)
        self.session.add(field)
        return field

    def delete(self, field: DatasetFieldRecord) -> None:
        self.session.delete(field)


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
    def __init__(self, session: Session) -> None:
        self.session = session

    def _available(self) -> bool:
        return inspect(self.session.get_bind()).has_table("dataset_records")

    def create(self, *, dataset_id: str, identifier_key: bytes) -> DatasetRecord:
        record = DatasetRecord(dataset_id=dataset_id, identifier_key=identifier_key)
        self.session.add(record)
        return record

    def add_values(self, record: DatasetRecord, values: tuple[RecordValue, ...]) -> None:
        rows = []
        for value in values:
            typed = {
                "string_value": None,
                "number_value": None,
                "boolean_value": None,
                "date_value": None,
            }
            typed[f"{value.data_type}_value"] = value.value
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
                .where(DatasetRecord.dataset_id == dataset_id)
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
            value = next(
                candidate
                for candidate in (
                    row.string_value,
                    row.number_value,
                    row.boolean_value,
                    row.date_value,
                )
                if candidate is not None
            )
            if field.data_type == "number":
                value = Decimal(row.canonical_value_key[1:].decode("ascii"))
            grouped[row.record_id].append(
                RecordValue(
                    field_id=field.id,
                    field_key=field.key,
                    data_type=field.data_type,
                    value=value,
                    canonical_key=row.canonical_value_key,
                    is_unique=field.is_unique,
                )
            )
        return [
            StoredRecord(record.id, record.dataset_id, tuple(grouped[record.id]))
            for record in records
        ]

    def count(self, dataset_id: str) -> int:
        if not self._available():
            return 0
        return (
            self.session.scalar(
                select(func.count(DatasetRecord.id)).where(DatasetRecord.dataset_id == dataset_id)
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


class SqlAlchemyUnitOfWork:
    def __init__(
        self, sessions: sessionmaker[Session], *, write: bool = False, serialized: bool = False
    ) -> None:
        self.sessions = sessions
        self.write = write
        self.serialized = serialized

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self.sessions()
        if self.write:
            if self.serialized and self.session.get_bind().dialect.name == "sqlite":
                self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            else:
                self.session.begin()
        self.users = SqlAlchemyUserOperations(self.session)
        self.teams = SqlAlchemyTeamOperations(self.session)
        self.memberships = SqlAlchemyMembershipOperations(self.session)
        self.grants = SqlAlchemyGrantOperations(self.session)
        self.datasets = SqlAlchemyDatasetOperations(self.session)
        self.dataset_fields = SqlAlchemyDatasetFieldOperations(self.session)
        self.dataset_business_keys = SqlAlchemyDatasetBusinessKeyOperations(self.session)
        self.dataset_records = SqlAlchemyDatasetRecordOperations(self.session)
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
                    raise
                except Exception:
                    self.session.rollback()
                    raise
            elif self.write:
                self.session.rollback()
        finally:
            self.session.close()
        return False

    def flush(self) -> None:
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise PersistenceConflictError(_safe_constraint_name(exc)) from None

    def add_audit(self, **values) -> None:
        self.session.add(AuditEvent(**values))


class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def __call__(self, *, write: bool = False, serialized: bool = False) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.sessions, write=write, serialized=serialized)


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

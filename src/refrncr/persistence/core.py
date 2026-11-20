from __future__ import annotations

from types import TracebackType
from typing import Literal

from sqlalchemy import (
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from refrncr.clock import SYSTEM_CLOCK, Clock
from refrncr.persistence.api_keys import SqlAlchemyApiKeyOperations
from refrncr.persistence.audit_operations import (
    SqlAlchemyAuditReadOperations,
    SqlAlchemyInstallationSettingsOperations,
)
from refrncr.persistence.common import safe_constraint_name
from refrncr.persistence.concepts import SqlAlchemyConceptOperations
from refrncr.persistence.dataset_operations import (
    SqlAlchemyDatasetBusinessKeyOperations,
    SqlAlchemyDatasetFieldOperations,
    SqlAlchemyDatasetOperations,
)
from refrncr.persistence.diff_operations import SqlAlchemyDiffOperations
from refrncr.persistence.hierarchies import SqlAlchemyHierarchyOperations
from refrncr.persistence.identity_operations import (
    SqlAlchemyGrantOperations,
    SqlAlchemyMembershipOperations,
    SqlAlchemyTeamOperations,
    SqlAlchemyUserOperations,
)
from refrncr.persistence.models import (
    AuditEvent,
    Dataset,
    DatasetExportAccess,
    Grant,
    Membership,
    User,
)
from refrncr.persistence.overview import SqlAlchemyOverviewOperations
from refrncr.persistence.record_operations import SqlAlchemyDatasetRecordOperations
from refrncr.persistence.versions import SqlAlchemyVersionOperations
from refrncr.ports import (
    AuthorizationSubject,
    PasswordHashReplacementError,
    PermissionContext,
    PersistenceConflictError,
    ResourcePermissionsContext,
)


class SqlAlchemyUnitOfWork:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        write: bool = False,
        serialized: bool = False,
        translate_errors: bool = False,
        clock: Clock = SYSTEM_CLOCK,
    ) -> None:
        self.sessions = sessions
        self.write = write
        self.serialized = serialized
        self.translate_errors = translate_errors
        self.clock = clock

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self.sessions()
        self.session.info["clock"] = self.clock
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
        self.installation_settings = SqlAlchemyInstallationSettingsOperations(self.session)
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

        self.hierarchy = SqlAlchemyHierarchyOperations(self.session)

        self.versions = SqlAlchemyVersionOperations(self.session)

        self.version_diff = SqlAlchemyDiffOperations(self.session)

        self.concepts = SqlAlchemyConceptOperations(self.session)

        self.overview = SqlAlchemyOverviewOperations(self.session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
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
            raise PersistenceConflictError(safe_constraint_name(exc)) from None

    def now(self):
        return self.clock.now()

    def today(self):
        return self.clock.today()

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
    def __init__(self, sessions: sessionmaker[Session], clock: Clock = SYSTEM_CLOCK) -> None:
        self.sessions = sessions
        self.clock = clock

    def __call__(
        self, *, write: bool = False, serialized: bool = False, translate_errors: bool = False
    ) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(
            self.sessions,
            write=write,
            serialized=serialized,
            translate_errors=translate_errors,
            clock=self.clock,
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
            return ResourcePermissionsContext(
                subject,
                tuple(tuple(row) for row in permissions.all()),
            )


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

    def exportable_dataset_ids(self, user_id, team_ids):
        granted = select(DatasetExportAccess.dataset_id).where(
            or_(
                DatasetExportAccess.user_id == user_id,
                DatasetExportAccess.team_id.in_(team_ids),
            )
        )
        return tuple(
            self.session.scalars(
                select(Dataset.id)
                .where(or_(Dataset.owner_team_id.in_(team_ids), Dataset.id.in_(granted)))
                .order_by(Dataset.id)
            )
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

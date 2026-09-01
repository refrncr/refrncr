from __future__ import annotations

from types import TracebackType

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from refrncr.models import (
    AuditEvent,
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
    GrantRecord,
    MembershipRecord,
    PasswordHashReplacementError,
    PermissionContext,
    PersistenceConflictError,
    TeamRecord,
)


def _locked(statement, session: Session, for_update: bool):
    if for_update and session.get_bind().dialect.name != "sqlite":
        return statement.with_for_update()
    return statement


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
            raise PersistenceConflictError from exc

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

from __future__ import annotations

from sqlalchemy import (
    func,
    select,
    update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from refrncr.persistence.common import clock_now, locked
from refrncr.persistence.models import (
    Grant,
    LoginSession,
    Membership,
    Organisation,
    Team,
    User,
)
from refrncr.ports import (
    GrantRecord,
    MembershipRecord,
    MembershipRelationship,
    PasswordHashReplacementError,
    PersistenceConflictError,
    TeamRecord,
)


class SqlAlchemyUserOperations:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, user_id: str, *, for_update: bool = False) -> User | None:
        statement = locked(select(User).where(User.id == user_id), self.session, for_update)
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
                .values(password_hash=replacement_hash, updated_at=clock_now(self.session))
            )
            self.session.flush()
        except SQLAlchemyError:
            raise PasswordHashReplacementError("Password hash replacement failed") from None
        return result.rowcount == 1

    def update_theme_preference(self, user_id: str, theme_preference: str) -> User:
        user = self.get(user_id, for_update=True)
        if user is None:
            raise PersistenceConflictError("User no longer exists")
        user.theme_preference = theme_preference
        self.session.flush()
        return user

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
        statement = locked(
            select(Organisation).where(Organisation.id == "community"), self.session, True
        )
        return self.session.scalar(statement)


class SqlAlchemyTeamOperations:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, team_id: str, *, for_update: bool = False) -> Team | None:
        statement = locked(select(Team).where(Team.id == team_id), self.session, for_update)
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
        statement = locked(
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
        return [tuple(row) for row in self.session.execute(statement).all()]

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
        statement = locked(select(Grant).where(Grant.id == grant_id), self.session, for_update)
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
        statement = locked(statement, self.session, for_update)
        return list(self.session.scalars(statement))

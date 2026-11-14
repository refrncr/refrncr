"""Identity team-membership service."""

from refrncr.identity.errors import (
    IdentityConflictError,
    IdentityNotFoundError,
    InvalidLifecycleError,
)
from refrncr.identity.shared import require_admin
from refrncr.identity.users import UserService
from refrncr.ports import (
    AuditEntry,
    MembershipRecord,
    PersistenceConflictError,
    TeamRecord,
    UnitOfWork,
    UnitOfWorkFactory,
    UserRecord,
)


class MembershipService:
    """Team membership operations within the one Community installation context."""

    def list_relationships(self, actor_id):
        """Administrator-only roster projection, loaded in one query."""
        with self.uow() as uow:
            require_admin(uow, actor_id)
            return tuple(
                sorted(
                    uow.memberships.list_relationships(),
                    key=lambda r: (
                        r.team_name.casefold(),
                        r.team_id,
                        r.user_name.casefold(),
                        r.user_id,
                    ),
                )
            )

    def __init__(self, uow: UnitOfWorkFactory):
        self.uow = uow

    def create_membership(self, actor_id: str, user_id: str, team_id: str) -> MembershipRecord:
        with self.uow(write=True, serialized=True) as uow:
            actor = require_admin(uow, actor_id)
            self._lock_team(uow, team_id)
            target = self._lock_user(uow, user_id)
            if not target.is_active or target.is_deleted:
                raise InvalidLifecycleError("Only active users can be added to teams")
            membership = uow.memberships.create(user_id=target.id, team_id=team_id)
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                raise IdentityConflictError("User is already a member of this team") from exc
            self._add_audit(uow, self._audit(actor.id, membership.id, "created"))
            return membership

    def delete_membership(self, actor_id: str, team_id: str, membership_id: str) -> None:
        with self.uow(write=True, serialized=True) as uow:
            actor = require_admin(uow, actor_id)
            membership = uow.memberships.get(membership_id)
            if membership is None or membership.team_id != team_id:
                raise IdentityNotFoundError("Membership not found")
            self._lock_team(uow, team_id)
            membership = uow.memberships.get(membership_id, for_update=True)
            if membership is None or membership.team_id != team_id:
                raise IdentityNotFoundError("Membership not found")
            self._add_audit(uow, self._audit(actor.id, membership.id, "deleted"))
            uow.memberships.delete(membership)

    def list_team_members(
        self, actor_id: str, team_id: str
    ) -> list[tuple[MembershipRecord, UserRecord]]:
        with self.uow() as uow:
            require_admin(uow, actor_id)
            self._team(uow, team_id)
            return uow.memberships.list_team_members(team_id)

    def list_eligible_users(self, actor_id: str, team_id: str) -> list[UserRecord]:
        with self.uow() as uow:
            require_admin(uow, actor_id)
            self._team(uow, team_id)
            return uow.memberships.list_eligible_users(team_id)

    @staticmethod
    def _team(uow: UnitOfWork, team_id: str) -> TeamRecord:
        team = uow.teams.get(team_id)
        if team is None:
            raise IdentityNotFoundError("Team not found")
        return team

    @staticmethod
    def _lock_team(uow: UnitOfWork, team_id: str) -> TeamRecord:
        team = uow.teams.get(team_id, for_update=True)
        if team is None:
            raise IdentityNotFoundError("Team not found")
        return team

    @staticmethod
    def _lock_user(uow: UnitOfWork, user_id: str) -> UserRecord:
        user = uow.users.get(user_id, for_update=True)
        if user is None:
            raise IdentityNotFoundError("User not found")
        return user

    @staticmethod
    def _audit(actor_id: str, membership_id: str, action: str) -> AuditEntry:
        return AuditEntry(
            actor_user_id=actor_id,
            object_type="membership",
            object_id=membership_id,
            action=action,
        )

    _add_audit = staticmethod(UserService._add_audit)

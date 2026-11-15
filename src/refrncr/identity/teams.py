"""Identity team service."""

from refrncr.identity.errors import (
    IdentityAuthorizationError,
    IdentityConflictError,
    IdentityNotFoundError,
    InvalidLifecycleError,
)
from refrncr.identity.shared import normalize_team_name, require_admin
from refrncr.identity.users import UserService
from refrncr.ports import (
    AuditEntry,
    PersistenceConflictError,
    TeamRecord,
    UnitOfWork,
    UnitOfWorkFactory,
)


class TeamService:
    """Team operations within the one Community installation context."""

    def __init__(self, uow: UnitOfWorkFactory):
        self.uow = uow

    def create_team(self, actor_id: str, name: str) -> TeamRecord:
        with self.uow(write=True, serialized=True) as uow:
            actor = require_admin(uow, actor_id)
            team = uow.teams.create(name=normalize_team_name(name))
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                raise IdentityConflictError(
                    "Team name already exists in this installation"
                ) from exc
            self._add_audit(uow, self._audit(actor.id, team.id, "created"))
            return team

    def rename_team(self, actor_id: str, team_id: str, name: str) -> TeamRecord:
        with self.uow(write=True, serialized=True) as uow:
            actor = require_admin(uow, actor_id)
            team = self._target(uow, team_id, for_update=True)
            normalized = normalize_team_name(name)
            if team.name == normalized:
                return team
            team.name = normalized
            team.updated_at = uow.now()
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                raise IdentityConflictError(
                    "Team name already exists in this installation"
                ) from exc
            self._add_audit(uow, self._audit(actor.id, team.id, "renamed"))
            return team

    def delete_team(self, actor_id: str, team_id: str) -> None:
        with self.uow(write=True, serialized=True) as uow:
            actor = require_admin(uow, actor_id)
            team = self._target(uow, team_id, for_update=True)
            if uow.datasets.owns_datasets(team.id):
                raise InvalidLifecycleError(
                    "Reassign this team’s datasets before deleting the team."
                )
            for grant in uow.grants.list_for_team(team.id, for_update=True):
                self._add_audit(uow, self._grant_audit(actor.id, grant.id))
                uow.grants.delete(grant)
            self._add_audit(uow, self._audit(actor.id, team.id, "deleted"))
            uow.teams.delete(team)

    def list_teams(self, actor_id: str) -> list[TeamRecord]:
        with self.uow() as uow:
            actor = uow.users.get(actor_id)
            if actor is None or not actor.is_active or actor.is_deleted:
                raise IdentityAuthorizationError("Active user access required")
            teams = uow.teams.list_by_name()
            if actor.role == "admin":
                return teams
            ids = uow.memberships.team_ids_for_user(actor_id)
            return [team for team in teams if team.id in ids]

    @staticmethod
    def _target(uow: UnitOfWork, team_id: str, *, for_update: bool = False) -> TeamRecord:
        team = uow.teams.get(team_id, for_update=for_update)
        if team is None:
            raise IdentityNotFoundError("Team not found")
        return team

    @staticmethod
    def _audit(actor_id: str, team_id: str, action: str) -> AuditEntry:
        return AuditEntry(
            actor_user_id=actor_id,
            object_type="team",
            object_id=team_id,
            action=action,
        )

    @staticmethod
    def _grant_audit(actor_id: str, grant_id: str) -> AuditEntry:
        return AuditEntry(
            actor_user_id=actor_id,
            object_type="grant",
            object_id=grant_id,
            action="deleted",
        )

    _add_audit = staticmethod(UserService._add_audit)

"""Dataset capability decisions."""

from contextlib import nullcontext

from refrncr.domain import valid_identity
from refrncr.errors import NotFoundError
from refrncr.ports import TeamRecord, UnitOfWorkFactory

from .errors import AccessManagementAuthorizationError
from .models import AuthorizedResourceSet


class DatasetAuthorizationService:
    """Dataset capabilities, independent of frameworks and legacy resource roles."""

    def __init__(self, uow: UnitOfWorkFactory):
        self.uow = uow

    @staticmethod
    def active(actor):
        return actor is not None and actor.is_active and not actor.is_deleted

    def creation_teams(self, actor_id: str, *, uow=None) -> list[TeamRecord]:
        with nullcontext(uow) if uow is not None else self.uow() as work:
            actor = work.users.get(actor_id)
            if not self.active(actor):
                raise AccessManagementAuthorizationError("Active user access required")
            teams = work.teams.list_by_name()
            if actor.role == "admin":
                return teams
            memberships = set(work.memberships.team_ids_for_user(actor_id))
            return [team for team in teams if team.id in memberships]

    def can_create_dataset(self, actor_id, team_id=None, *, uow=None):
        with nullcontext(uow) if uow is not None else self.uow() as work:
            if team_id is None:
                return self.active(work.users.get(actor_id))
            if not valid_identity(team_id):
                return False
            team = work.teams.get(team_id, for_update=uow is not None)
            actor = work.users.get(actor_id, for_update=uow is not None)
            return bool(
                team
                and self.active(actor)
                and (
                    actor.role == "admin" or team_id in work.memberships.team_ids_for_user(actor_id)
                )
            )

    def _decision(self, actor_id, dataset_id, capability, *, uow=None):
        with nullcontext(uow) if uow is not None else self.uow() as work:
            dataset = work.datasets.get(dataset_id)
            if dataset is None:
                return False
            mutation = uow is not None and work.write and capability not in {"view", "export"}
            if mutation and capability == "maintain":
                if work.teams.get(dataset.owner_team_id, for_update=True) is None:
                    return False
            actor = work.users.get(actor_id, for_update=mutation)
            if not self.active(actor):
                return False
            if capability in {"export", "manage_export"}:
                policy = work.installation_settings.get_policy()
                if policy == "everyone":
                    return capability == "export"
                if capability == "manage_export" and policy == "admin_grants":
                    return actor.role == "admin"
            if capability == "view" or actor.role == "admin":
                return True
            if capability == "change_owner":
                return False
            teams = work.memberships.team_ids_for_user(actor_id)
            if dataset.owner_team_id in teams:
                return True
            return capability == "export" and work.dataset_export_access.applies(
                dataset.id, actor_id, teams
            )

    def can_view_dataset(self, actor_id, dataset_id, *, uow=None):
        return self._decision(actor_id, dataset_id, "view", uow=uow)

    def viewable_resources(self, actor_id, *, uow=None):
        with nullcontext(uow) if uow is not None else self.uow() as work:
            if not self.active(work.users.get(actor_id)):
                raise NotFoundError("Dataset not found")
            return AuthorizedResourceSet(True, ())

    def exportable_resources(self, actor_id, *, uow=None):
        with nullcontext(uow) if uow is not None else self.uow() as work:
            actor = work.users.get(actor_id)
            if not self.active(actor):
                raise NotFoundError("Dataset not found")
            policy = work.installation_settings.get_policy()
            if policy == "everyone" or actor.role == "admin":
                return AuthorizedResourceSet(True, ())
            teams = work.memberships.team_ids_for_user(actor_id)
            return AuthorizedResourceSet(
                False, work.dataset_export_access.exportable_dataset_ids(actor_id, teams)
            )

    def maintainable_resources(self, actor_id, *, uow=None):
        with nullcontext(uow) if uow is not None else self.uow() as work:
            actor = work.users.get(actor_id)
            if not self.active(actor):
                raise AccessManagementAuthorizationError("Active user access required")
            if actor.role == "admin":
                return AuthorizedResourceSet(True, ())
            teams = set(work.memberships.team_ids_for_user(actor_id))
            return AuthorizedResourceSet(
                False,
                tuple(item.id for item in work.datasets.list_all() if item.owner_team_id in teams),
            )

    def can_maintain_dataset(self, actor_id, dataset_id, *, uow=None):
        return self._decision(actor_id, dataset_id, "maintain", uow=uow)

    def can_export_dataset(self, actor_id, dataset_id, *, uow=None):
        return self._decision(actor_id, dataset_id, "export", uow=uow)

    def can_change_dataset_owner(self, actor_id, dataset_id, *, uow=None):
        return self._decision(actor_id, dataset_id, "change_owner", uow=uow)

    def can_manage_export_access(self, actor_id, dataset_id, *, uow=None):
        return self._decision(actor_id, dataset_id, "manage_export", uow=uow)

    def can_administer_installation(self, actor_id, *, uow=None):
        with nullcontext(uow) if uow is not None else self.uow() as work:
            actor = work.users.get(actor_id)
            return self.active(actor) and actor.role == "admin"

    def can_access_community(self, actor_id, *, uow=None):
        with nullcontext(uow) if uow is not None else self.uow() as work:
            return self.active(work.users.get(actor_id))

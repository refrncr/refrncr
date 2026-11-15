"""Deprecated resource-grant compatibility service."""

from refrncr.audit import record_audit
from refrncr.domain import valid_identity
from refrncr.identity import InvalidLifecycleError, require_admin
from refrncr.ports import (
    AuditEntry,
    GrantRecord,
    PersistenceConflictError,
    UnitOfWork,
    UnitOfWorkFactory,
)

from .errors import (
    AccessManagementConflictError,
    AccessManagementNotFoundError,
    AccessManagementValidationError,
)
from .permissions import validate_permission
from .resource_types import ResourceTypeRegistry

SUBJECT_TYPES = {"user", "team"}


class GrantService:
    """Deprecated compatibility service for historical resource Grants."""

    def __init__(self, uow: UnitOfWorkFactory, resources: ResourceTypeRegistry) -> None:
        self.uow = uow
        self.resources = resources

    def create_grant(
        self,
        actor_id: str,
        subject_type: str,
        subject_id: str,
        resource_type: str,
        resource_id: str,
        permission: str,
    ) -> GrantRecord:
        with self.uow(write=True, serialized=True) as uow:
            dataset = (
                uow.datasets.get(resource_id, for_update=True)
                if resource_type == "dataset" and valid_identity(resource_id)
                else None
            )
            actor = require_admin(uow, actor_id)
            if resource_type == "dataset" and dataset is None:
                raise AccessManagementNotFoundError("Resource not found")
            self._validate_permission(permission)
            if subject_type not in SUBJECT_TYPES:
                raise AccessManagementValidationError("Subject type must be user or team")
            self.resources.require_existing(resource_type, resource_id)
            self._require_subject(uow, subject_type, subject_id)
            grant = uow.grants.create(
                subject_type=subject_type,
                subject_id=subject_id,
                resource_type=resource_type,
                resource_id=resource_id,
                permission=permission,
            )
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                raise AccessManagementConflictError(
                    "Subject already has a Grant for this resource"
                ) from exc
            self._add_audit(uow, self._audit(actor.id, grant.id, "created"))
            return grant

    def change_permission(self, actor_id: str, grant_id: str, permission: str) -> GrantRecord:
        with self.uow(write=True, serialized=True) as uow:
            actor = require_admin(uow, actor_id)
            self._validate_permission(permission)
            grant = self._target(uow, grant_id, for_update=True)
            if grant.permission == permission:
                return grant
            grant.permission = permission
            grant.updated_at = uow.now()
            self._add_audit(uow, self._audit(actor.id, grant.id, "permission_changed"))
            return grant

    def delete_grant(self, actor_id: str, grant_id: str) -> None:
        with self.uow(write=True, serialized=True) as uow:
            actor = require_admin(uow, actor_id)
            grant = self._target(uow, grant_id, for_update=True)
            self._add_audit(uow, self._audit(actor.id, grant.id, "deleted"))
            uow.grants.delete(grant)

    @staticmethod
    def _validate_permission(permission: str) -> None:
        try:
            validate_permission(permission)
        except ValueError as exc:
            raise AccessManagementValidationError(
                "Permission must be viewer, editor or admin"
            ) from exc

    @staticmethod
    def _require_subject(uow: UnitOfWork, subject_type: str, subject_id: str) -> None:
        if subject_type == "user":
            subject = uow.users.get(subject_id, for_update=True)
            if subject is None:
                raise AccessManagementNotFoundError("User not found")
            if not subject.is_active or subject.is_deleted:
                raise InvalidLifecycleError("Only active users can receive direct Grants")
            return
        if uow.teams.get(subject_id, for_update=True) is None:
            raise AccessManagementNotFoundError("Team not found")

    @staticmethod
    def _target(uow: UnitOfWork, grant_id: str, *, for_update: bool = False) -> GrantRecord:
        grant = uow.grants.get(grant_id, for_update=for_update)
        if grant is None:
            raise AccessManagementNotFoundError("Grant not found")
        return grant

    @staticmethod
    def _audit(actor_id: str, grant_id: str, action: str) -> AuditEntry:
        return AuditEntry(
            actor_user_id=actor_id,
            object_type="grant",
            object_id=grant_id,
            action=action,
        )

    @staticmethod
    def _add_audit(uow: UnitOfWork, audit: AuditEntry) -> None:
        record_audit(
            uow,
            actor_user_id=audit.actor_user_id,
            object_type=audit.object_type,
            object_id=audit.object_id,
            action=audit.action,
        )


def create_deprecated_grant_service(
    uow: UnitOfWorkFactory, resources: ResourceTypeRegistry
) -> GrantService:
    """Create the isolated Grant compatibility service for non-web callers."""
    return GrantService(uow, resources)

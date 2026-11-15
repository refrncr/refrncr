"""Community authorization service."""

from refrncr.ports import PermissionDataSource

from .errors import AccessManagementAuthorizationError, AccessManagementValidationError
from .grants import GrantService
from .models import AuthorizedResourceSet
from .permissions import highest_permission, permits
from .resource_types import ResourceTypeRegistry


class AuthorizationService:
    """Single resource-permission engine plus the separate Community admin rule."""

    def __init__(
        self, data: PermissionDataSource, resources: ResourceTypeRegistry, datasets=None
    ) -> None:
        self.datasets = datasets
        self.data = data
        self.resources = resources

    def effective_permission(
        self, user_id: str, resource_type: str, resource_id: str
    ) -> str | None:
        if resource_type == "dataset":
            return None
        context = self.data.load_context(user_id, resource_type, resource_id)
        if not context.subject.exists or not context.subject.active or context.subject.deleted:
            return None
        self.resources.require_existing(resource_type, resource_id)
        return highest_permission(context.permissions)

    def check_access(
        self, user_id: str, resource_type: str, resource_id: str, required: str, *, uow=None
    ) -> bool:
        """Deprecated GrantService compatibility API; use has_capability instead."""
        if resource_type == "dataset":
            if self.datasets is None:
                return False
            decisions = {
                "viewer": self.datasets.can_view_dataset,
                "editor": self.datasets.can_maintain_dataset,
                "admin": self.datasets.can_maintain_dataset,
                "export": self.datasets.can_export_dataset,
            }
            if required not in decisions:
                raise AccessManagementValidationError("Unknown Dataset capability")
            return decisions[required](user_id, resource_id, uow=uow)
        GrantService._validate_permission(required)
        context = self.data.load_context(user_id, resource_type, resource_id)
        if context.subject.exists and context.subject.active and not context.subject.deleted:
            self.resources.require_existing(resource_type, resource_id)
        return permits(context.subject, context.permissions, required)

    def has_capability(self, user_id: str, capability: str, resource_id=None, *, uow=None) -> bool:
        """Evaluate a named Community capability at one authorization boundary."""
        installation_capabilities = {
            "can_access_community": (
                getattr(self.datasets, "can_access_community", None) if self.datasets else None
            ),
            "can_administer_installation": (
                getattr(self.datasets, "can_administer_installation", None)
                if self.datasets
                else None
            ),
            "can_manage_access": (
                getattr(self.datasets, "can_administer_installation", None)
                if self.datasets
                else None
            ),
            "can_create_dataset": (
                getattr(self.datasets, "can_create_dataset", None) if self.datasets else None
            ),
        }
        if capability in installation_capabilities:
            decision = installation_capabilities[capability]
            if resource_id is not None or decision is None:
                raise AccessManagementValidationError(
                    f"Unknown or incomplete capability: {capability}"
                )
            return decision(user_id, uow=uow)
        dataset_capabilities = {
            "can_view_dataset": self.datasets.can_view_dataset if self.datasets else None,
            "can_maintain_dataset": (self.datasets.can_maintain_dataset if self.datasets else None),
            "can_export_dataset": self.datasets.can_export_dataset if self.datasets else None,
            "can_change_dataset_owner": (
                self.datasets.can_change_dataset_owner if self.datasets else None
            ),
            "can_manage_export_access": (
                self.datasets.can_manage_export_access if self.datasets else None
            ),
        }
        decision = dataset_capabilities.get(capability)
        if decision is None or resource_id is None:
            raise AccessManagementValidationError(f"Unknown or incomplete capability: {capability}")
        return decision(user_id, resource_id, uow=uow)

    def authorized_resources(
        self, user_id: str, resource_type: str, required: str
    ) -> AuthorizedResourceSet:
        GrantService._validate_permission(required)
        if resource_type == "dataset":
            if self.datasets is None or not self.datasets.can_create_dataset(user_id):
                raise AccessManagementAuthorizationError("Active user access required")
            if required == "viewer":
                return AuthorizedResourceSet(True, ())
            return self.datasets.maintainable_resources(user_id)
        context = self.data.load_resource_permissions(user_id, resource_type)
        subject = context.subject
        if not subject.exists or not subject.active or subject.deleted:
            raise AccessManagementAuthorizationError("Active user access required")
        if subject.community_admin:
            return AuthorizedResourceSet(all_resources=True, resource_ids=())

        permissions_by_resource: dict[str, list[str]] = {}
        for resource_id, permission in context.permissions:
            permissions_by_resource.setdefault(resource_id, []).append(permission)
        resource_ids = tuple(
            sorted(
                resource_id
                for resource_id, permissions in permissions_by_resource.items()
                if permits(subject, permissions, required)
            )
        )
        return AuthorizedResourceSet(all_resources=False, resource_ids=resource_ids)

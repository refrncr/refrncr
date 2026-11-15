"""Authorization and access-management domain package."""

from .dataset_resources import DatasetResourceResolver
from .datasets import DatasetAuthorizationService
from .errors import (
    AccessManagementAuthorizationError,
    AccessManagementConflictError,
    AccessManagementNotFoundError,
    AccessManagementValidationError,
)
from .grants import GrantService, create_deprecated_grant_service
from .installation_policy import (
    EXPORT_POLICIES,
    INSTALLATION_SETTINGS_ID,
    policy_confirmation,
)
from .installation_settings import InstallationSettingsService, PolicyConfirmationRequired
from .models import AuthorizedResourceSet
from .permissions import highest_permission, permits, validate_permission
from .resource_types import ResourceTypeRegistry
from .service import AuthorizationService

__all__ = (
    "AccessManagementAuthorizationError",
    "AccessManagementConflictError",
    "AccessManagementNotFoundError",
    "AccessManagementValidationError",
    "AuthorizationService",
    "AuthorizedResourceSet",
    "DatasetAuthorizationService",
    "DatasetResourceResolver",
    "EXPORT_POLICIES",
    "GrantService",
    "INSTALLATION_SETTINGS_ID",
    "InstallationSettingsService",
    "PolicyConfirmationRequired",
    "ResourceTypeRegistry",
    "create_deprecated_grant_service",
    "highest_permission",
    "permits",
    "policy_confirmation",
    "validate_permission",
)

"""Registry of grantable resource types."""

from collections.abc import Callable

from .errors import (
    AccessManagementConflictError,
    AccessManagementNotFoundError,
    AccessManagementValidationError,
)


class ResourceTypeRegistry:
    """Maps canonical resource types to framework-independent existence resolvers."""

    def __init__(self) -> None:
        self._resolvers: dict[str, Callable[[str], bool]] = {}

    def register(self, resource_type: str, resolver: Callable[[str], bool]) -> None:
        if (
            not resource_type
            or resource_type != resource_type.strip().casefold()
            or len(resource_type) > 100
        ):
            raise AccessManagementValidationError(
                "Resource type must be a canonical lower-case value"
            )
        if resource_type in self._resolvers:
            raise AccessManagementConflictError("Resource type is already registered")
        self._resolvers[resource_type] = resolver

    def require_existing(self, resource_type: str, resource_id: str) -> None:
        resolver = self._resolvers.get(resource_type)
        if resolver is None:
            raise AccessManagementValidationError("Resource type is not registered")
        if not resource_id or len(resource_id) > 100 or not resolver(resource_id):
            raise AccessManagementNotFoundError("Grantable resource not found")

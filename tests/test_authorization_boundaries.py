from dataclasses import dataclass

import pytest

from refrncr.authorization import highest_permission, permits
from refrncr.ports import AuthorizationSubject, PermissionContext
from refrncr.services import AuthorizationService, ResourceTypeRegistry, ValidationError


@pytest.mark.parametrize(
    ("permissions", "expected"),
    [
        ([], None),
        (["viewer"], "viewer"),
        (["viewer", "editor", "viewer"], "editor"),
        (["admin", "editor", "admin"], "admin"),
    ],
)
def test_highest_permission_is_pure_and_deterministic(permissions, expected):
    assert highest_permission(permissions) == expected


@pytest.mark.parametrize(
    "subject",
    [
        AuthorizationSubject(False, False, False, False),
        AuthorizationSubject(True, False, False, False),
        AuthorizationSubject(True, False, True, False),
    ],
)
def test_ineligible_subjects_are_default_deny(subject):
    assert not permits(subject, ["admin"], "viewer")


def test_community_administrator_override_is_separate_from_resource_admin():
    community_admin = AuthorizationSubject(True, True, False, True)
    regular_user = AuthorizationSubject(True, True, False, False)

    assert permits(community_admin, [], "admin")
    assert permits(regular_user, ["admin"], "admin")
    assert not regular_user.community_admin


@dataclass
class FakePermissionDataSource:
    context: PermissionContext
    calls: list[tuple[str, str, str]]

    def load_context(self, user_id: str, resource_type: str, resource_id: str) -> PermissionContext:
        self.calls.append((user_id, resource_type, resource_id))
        return self.context


def test_authorization_service_uses_data_source_without_persistence():
    data = FakePermissionDataSource(
        PermissionContext(
            AuthorizationSubject(True, True, False, False),
            ("viewer", "admin", "editor", "admin"),
        ),
        [],
    )
    resources = ResourceTypeRegistry()
    resources.register("document", lambda resource_id: resource_id == "document-1")
    authorization = AuthorizationService(data, resources)

    assert authorization.effective_permission("user-1", "document", "document-1") == "admin"
    assert authorization.check_access("user-1", "document", "document-1", "admin")
    assert data.calls == [
        ("user-1", "document", "document-1"),
        ("user-1", "document", "document-1"),
    ]


def test_authorization_service_preserves_default_deny_and_admin_override():
    resources = ResourceTypeRegistry()
    resources.register("document", lambda _resource_id: True)
    missing = FakePermissionDataSource(
        PermissionContext(AuthorizationSubject(False, False, False, False), ("admin",)), []
    )
    admin = FakePermissionDataSource(
        PermissionContext(AuthorizationSubject(True, True, False, True), ()), []
    )

    assert (
        AuthorizationService(missing, resources).effective_permission("missing", "document", "1")
        is None
    )
    assert not AuthorizationService(missing, resources).check_access(
        "missing", "document", "1", "viewer"
    )
    assert (
        AuthorizationService(admin, resources).effective_permission("admin", "document", "1")
        is None
    )
    assert AuthorizationService(admin, resources).check_access("admin", "document", "1", "admin")


@dataclass
class FakeDatasetCapabilities:
    allowed: set[tuple[str, str, str]]

    def can_view_dataset(self, user_id, dataset_id, *, uow=None):
        return (user_id, "can_view_dataset", dataset_id) in self.allowed

    def can_maintain_dataset(self, user_id, dataset_id, *, uow=None):
        return (user_id, "can_maintain_dataset", dataset_id) in self.allowed

    def can_export_dataset(self, user_id, dataset_id, *, uow=None):
        return (user_id, "can_export_dataset", dataset_id) in self.allowed

    def can_change_dataset_owner(self, user_id, dataset_id, *, uow=None):
        return (user_id, "can_change_dataset_owner", dataset_id) in self.allowed

    def can_manage_export_access(self, user_id, dataset_id, *, uow=None):
        return (user_id, "can_manage_export_access", dataset_id) in self.allowed


def test_named_dataset_capabilities_are_the_service_boundary():
    datasets = FakeDatasetCapabilities({("user-1", "can_maintain_dataset", "dataset-1")})
    authorization = AuthorizationService(
        FakePermissionDataSource(
            PermissionContext(AuthorizationSubject(True, True, False, False), ()), []
        ),
        ResourceTypeRegistry(),
        datasets=datasets,
    )

    assert authorization.has_capability("user-1", "can_maintain_dataset", "dataset-1")
    assert not authorization.has_capability("user-1", "can_view_dataset", "dataset-1")

    with pytest.raises(ValidationError):
        authorization.has_capability("user-1", "editor", "dataset-1")

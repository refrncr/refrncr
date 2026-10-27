"""Installation-wide access policy, changed atomically with its audit event."""

from refrncr.audit import record_audit
from refrncr.installation_policy import (
    EXPORT_POLICIES,
    INSTALLATION_SETTINGS_ID,
    policy_confirmation,
)
from refrncr.services import AuthorizationError, ConflictError, ValidationError


class PolicyConfirmationRequired(ValidationError):
    """The exact transition needs explicit confirmation before it can be applied."""


class InstallationSettingsService:
    def __init__(self, uow, authorization):
        self.uow = uow
        self.authorization = authorization

    def get_policy(self, actor_id):
        with self.uow() as work:
            if not self.authorization.can_administer_installation(actor_id, uow=work):
                raise AuthorizationError("Administrator access required")
            return work.installation_settings.get_policy()

    def change_policy(self, actor_id, expected_policy, policy, *, confirmed=False):
        with self.uow(write=True, serialized=True) as work:
            current = work.installation_settings.get_policy(for_update=True)
            # Keep the actor locked while the shared capability check authorizes the write.
            work.users.get(actor_id, for_update=True)
            if not self.authorization.can_administer_installation(actor_id, uow=work):
                raise AuthorizationError("Administrator access required")
            if expected_policy not in EXPORT_POLICIES or policy not in EXPORT_POLICIES:
                raise ValidationError("Choose an available access policy.")
            if current != expected_policy:
                raise ConflictError("Dataset access policy changed. Reload the page and try again.")
            if current == policy:
                return current
            if policy_confirmation(current, policy) and confirmed is not True:
                raise PolicyConfirmationRequired(policy_confirmation(current, policy))
            if not work.installation_settings.change_policy(expected_policy, policy):
                raise ConflictError("Dataset access policy changed. Reload the page and try again.")
            record_audit(
                work,
                actor_user_id=actor_id,
                object_type="installation_settings",
                object_id=INSTALLATION_SETTINGS_ID,
                action="export_access_policy_changed",
                details={"previous_policy": current, "policy": policy},
            )
            return policy

"""Identity user service."""

from argon2 import PasswordHasher

from refrncr.audit import record_audit
from refrncr.identity.errors import (
    IdentityAuthorizationError,
    IdentityConflictError,
    IdentityNotFoundError,
    IdentityValidationError,
    InvalidLifecycleError,
)
from refrncr.identity.shared import (
    PASSWORD_HASHER,
    THEME_PREFERENCES,
    normalize_email,
    require_admin,
    validate_password,
)
from refrncr.ports import (
    AuditEntry,
    OrganisationRecord,
    PersistenceConflictError,
    UnitOfWork,
    UnitOfWorkFactory,
    UserRecord,
)


class UserService:
    """User operations within the one Community installation context."""

    def __init__(self, uow: UnitOfWorkFactory, hasher: PasswordHasher | None = None):
        self.uow = uow
        self.hasher = hasher or PASSWORD_HASHER

    def bootstrap_organisation(self) -> OrganisationRecord:
        try:
            with self.uow(write=True, serialized=True) as uow:
                organisation = uow.users.get_community_organisation()
                if organisation is None:
                    organisation = uow.users.create_community_organisation()
                    uow.flush()
                return organisation
        except PersistenceConflictError:
            # Another process may have inserted the singleton between our read and write.
            with self.uow() as uow:
                organisation = uow.users.get_community_organisation()
                if organisation is None:
                    raise
                return organisation

    def create_initial_admin(self, email: str, display_name: str, password: str) -> UserRecord:
        self.bootstrap_organisation()
        with self.uow(write=True, serialized=True) as uow:
            self._lock_organisation(uow)
            if uow.users.count() != 0:
                raise IdentityConflictError("Initial setup has already been completed")
            user = self._new_user(uow, email, display_name, password, "admin")
            uow.flush()
            self._add_audit(uow, self._audit(user.id, user, "created"))
            return user

    def has_users(self) -> bool:
        with self.uow() as uow:
            return uow.users.count() != 0

    def create_user(
        self,
        actor_id: str,
        email: str,
        display_name: str,
        password: str,
        role: str = "user",
    ) -> UserRecord:
        with self.uow(write=True) as uow:
            actor = require_admin(uow, actor_id)
            user = self._new_user(uow, email, display_name, password, role)
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                raise IdentityConflictError("Email already exists in this installation") from exc
            self._add_audit(uow, self._audit(actor.id, user, "created"))
            return user

    def change_email(self, actor_id: str, target_id: str, email: str) -> UserRecord:
        with self.uow(write=True, serialized=True) as uow:
            actor = require_admin(uow, actor_id)
            target = self._target(uow, target_id)
            normalized = normalize_email(email)
            if target.email == normalized:
                return target
            target.email = normalized
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                raise IdentityConflictError("Email already exists in this installation") from exc
            self._add_audit(uow, self._audit(actor.id, target, "email_changed"))
            return target

    def disable_user(self, actor_id: str, target_id: str) -> UserRecord:
        return self._set_state(actor_id, target_id, "disabled")

    def enable_user(self, actor_id: str, target_id: str) -> UserRecord:
        return self._set_state(actor_id, target_id, "enabled")

    def delete_user(self, actor_id: str, target_id: str) -> UserRecord:
        return self._set_state(actor_id, target_id, "deleted")

    def list_users(self, actor_id: str) -> list[UserRecord]:
        with self.uow() as uow:
            require_admin(uow, actor_id)
            return uow.users.list_by_email()

    def recover_administrator_password(self, email: str, password: str) -> UserRecord:
        """Replace an administrator credential for the local CLI or trusted integration.

        Community records a neutral audit event without inventing an actor. The caller
        must select the installation before constructing this service; this method only
        accepts an active administrator identity within that installation.
        """
        normalized_email = normalize_email(email)
        replacement_hash = self.hasher.hash(validate_password(password))
        with self.uow(write=True, serialized=True) as uow:
            self._lock_organisation(uow)
            candidate = uow.users.find_by_email(normalized_email)
            if candidate is None:
                raise IdentityNotFoundError("Administrator not found")
            target = self._target(uow, candidate.id)
            if target.role != "admin" or not target.is_active or target.is_deleted:
                raise IdentityNotFoundError("Administrator not found")
            if not uow.users.replace_password_hash(
                target.id, target.password_hash, replacement_hash
            ):
                raise IdentityConflictError("Administrator credential changed concurrently")
            uow.users.revoke_active_sessions(target.id, uow.now())
            self._add_audit(uow, self._audit(None, target, "password_recovered"))
            updated = uow.users.get(target.id)
            if updated is None:
                raise IdentityConflictError("Administrator credential changed concurrently")
            return updated

    def update_theme_preference(self, actor_id: str, theme_preference: str) -> UserRecord:
        if theme_preference not in THEME_PREFERENCES:
            raise IdentityValidationError("Choose System, Light or Dark")
        with self.uow(write=True) as uow:
            actor = uow.users.get(actor_id, for_update=True)
            if actor is None or not actor.is_active or actor.is_deleted:
                raise IdentityAuthorizationError("Active user access required")
            return uow.users.update_theme_preference(actor.id, theme_preference)

    def _set_state(self, actor_id: str, target_id: str, action: str) -> UserRecord:
        with self.uow(write=True, serialized=True) as uow:
            self._lock_organisation(uow)
            actor = require_admin(uow, actor_id)
            target = self._target(uow, target_id)
            if action == "enabled":
                if target.is_deleted:
                    raise InvalidLifecycleError("Deleted users cannot be enabled")
                if target.is_active:
                    return target
                target.is_active = True
            else:
                if (action == "disabled" and not target.is_active) or (
                    action == "deleted" and target.is_deleted
                ):
                    return target
                if target.role == "admin" and target.is_active and not target.is_deleted:
                    active_admins = uow.users.count_active_admins()
                    if active_admins == 1:
                        raise InvalidLifecycleError(
                            "The last active administrator cannot be removed"
                        )
                target.is_active = False
                if action == "deleted":
                    target.is_deleted = True
                uow.users.revoke_active_sessions(target.id, uow.now())
            target.updated_at = uow.now()
            self._add_audit(uow, self._audit(actor.id, target, action))
            return target

    def _new_user(
        self,
        uow: UnitOfWork,
        email: str,
        display_name: str,
        password: str,
        role: str,
    ) -> UserRecord:
        if role not in {"admin", "user"}:
            raise IdentityValidationError("Unknown role")
        if not display_name.strip():
            raise IdentityValidationError("Display name is required", field="display_name")
        return uow.users.create(
            email=normalize_email(email),
            display_name=display_name.strip(),
            password_hash=self.hasher.hash(validate_password(password)),
            role=role,
            theme_preference="system",
            is_active=True,
            is_deleted=False,
        )

    @staticmethod
    def _target(uow: UnitOfWork, target_id: str) -> UserRecord:
        target = uow.users.get(target_id, for_update=True)
        if target is None:
            raise IdentityNotFoundError("User not found")
        return target

    @staticmethod
    def _lock_organisation(uow: UnitOfWork) -> OrganisationRecord:
        organisation = uow.users.lock_community_setup()
        if organisation is None:
            raise IdentityConflictError("Community Organisation has not been bootstrapped")
        return organisation

    @staticmethod
    def _audit(actor_id: str | None, target: UserRecord, action: str) -> AuditEntry:
        return AuditEntry(
            actor_user_id=actor_id,
            object_type="user",
            object_id=target.id,
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

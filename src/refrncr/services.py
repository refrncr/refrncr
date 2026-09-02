from __future__ import annotations

import hashlib
import secrets
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from refrncr.authorization import highest_permission, permits, validate_permission
from refrncr.models import LoginSession, User, utc_now
from refrncr.ports import (
    AuditEntry,
    GrantRecord,
    MembershipRecord,
    OrganisationRecord,
    PasswordHashReplacementError,
    PermissionDataSource,
    PersistenceConflictError,
    TeamRecord,
    UnitOfWork,
    UnitOfWorkFactory,
    UserRecord,
)

SUBJECT_TYPES = {"user", "team"}

# Keep this security posture explicit so dependency default changes cannot weaken it:
# Argon2id with 64 MiB memory, three iterations, and four-way parallelism.
PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# A valid current-profile hash used only to equalize work for attempts that cannot
# safely verify a stored credential. Its plaintext is intentionally not a credential.
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "U29tZVN0YXRpY1NhbHQxNg$"
    "JvPgxylqfMveFSbsSWHHBEKbGOlnkLIjFBG0s33wtgE"
)


class UserManagementError(Exception):
    pass


class AuthorizationError(UserManagementError):
    pass


class ConflictError(UserManagementError):
    pass


class InvalidLifecycleError(UserManagementError):
    pass


class NotFoundError(UserManagementError):
    pass


class ValidationError(UserManagementError, ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    id: str


def validate_password(password: str) -> str:
    """Validate without trimming, normalizing, or otherwise rewriting a password."""
    if not isinstance(password, str):
        raise ValidationError("Password must be text")
    if len(password) < 12:
        raise ValidationError("Password must contain at least 12 characters")
    try:
        encoded_size = len(password.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValidationError("Password must be valid Unicode text") from exc
    if encoded_size > 1024:
        raise ValidationError("Password must not exceed 1024 UTF-8 bytes")
    return password


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
            raise ValidationError("Resource type must be a canonical lower-case value")
        if resource_type in self._resolvers:
            raise ConflictError("Resource type is already registered")
        self._resolvers[resource_type] = resolver

    def require_existing(self, resource_type: str, resource_id: str) -> None:
        resolver = self._resolvers.get(resource_type)
        if resolver is None:
            raise ValidationError("Resource type is not registered")
        if not resource_id or len(resource_id) > 100 or not resolver(resource_id):
            raise NotFoundError("Grantable resource not found")


def normalize_email(email: str) -> str:
    normalized = email.strip().casefold()
    if (
        len(normalized) > 320
        or normalized.count("@") != 1
        or any(character.isspace() or ord(character) < 32 for character in normalized)
    ):
        raise ValidationError("A valid email address is required")
    local_part, domain = normalized.split("@")
    if not local_part or not domain:
        raise ValidationError("A valid email address is required")
    return normalized


def normalize_team_name(name: str) -> str:
    normalized_name = unicodedata.normalize("NFC", name.strip())
    if not 1 <= len(normalized_name) <= 100:
        raise ValidationError("Team name must contain between 1 and 100 characters")
    return normalized_name


def require_admin(uow: UnitOfWork, actor_id: str) -> UserRecord:
    actor = uow.users.get(actor_id)
    if actor is None or actor.role != "admin" or not actor.is_active or actor.is_deleted:
        raise AuthorizationError("Administrator access required")
    return actor


def _now() -> datetime:
    return datetime.now(UTC)


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
                raise ConflictError("Initial setup has already been completed")
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
                raise ConflictError("Email already exists in this installation") from exc
            self._add_audit(uow, self._audit(actor.id, user, "created"))
            return user

    def change_email(self, actor_id: str, target_id: str, email: str) -> UserRecord:
        with self.uow(write=True) as uow:
            actor = require_admin(uow, actor_id)
            target = self._target(uow, target_id)
            target.email = normalize_email(email)
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                raise ConflictError("Email already exists in this installation") from exc
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

    def _set_state(self, actor_id: str, target_id: str, action: str) -> UserRecord:
        with self.uow(write=True, serialized=True) as uow:
            self._lock_organisation(uow)
            actor = require_admin(uow, actor_id)
            target = self._target(uow, target_id)
            if action == "enabled":
                if target.is_deleted:
                    raise InvalidLifecycleError("Deleted users cannot be enabled")
                target.is_active = True
            else:
                if target.role == "admin" and target.is_active and not target.is_deleted:
                    active_admins = uow.users.count_active_admins()
                    if active_admins == 1:
                        raise InvalidLifecycleError(
                            "The last active administrator cannot be removed"
                        )
                target.is_active = False
                if action == "deleted":
                    target.is_deleted = True
                uow.users.revoke_active_sessions(target.id, _now())
            target.updated_at = _now()
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
            raise ValidationError("Unknown role")
        if not display_name.strip():
            raise ValidationError("Display name is required")
        return uow.users.create(
            email=normalize_email(email),
            display_name=display_name.strip(),
            password_hash=self.hasher.hash(validate_password(password)),
            role=role,
            is_active=True,
            is_deleted=False,
        )

    @staticmethod
    def _target(uow: UnitOfWork, target_id: str) -> UserRecord:
        target = uow.users.get(target_id)
        if target is None:
            raise NotFoundError("User not found")
        return target

    @staticmethod
    def _lock_organisation(uow: UnitOfWork) -> OrganisationRecord:
        organisation = uow.users.lock_community_setup()
        if organisation is None:
            raise ConflictError("Community Organisation has not been bootstrapped")
        return organisation

    @staticmethod
    def _audit(actor_id: str, target: UserRecord, action: str) -> AuditEntry:
        return AuditEntry(
            actor_user_id=actor_id,
            object_type="user",
            object_id=target.id,
            action=action,
        )

    @staticmethod
    def _add_audit(uow: UnitOfWork, audit: AuditEntry) -> None:
        uow.add_audit(
            actor_user_id=audit.actor_user_id,
            object_type=audit.object_type,
            object_id=audit.object_id,
            action=audit.action,
        )


class AuthenticationService:
    """Persistence-neutral credential verification and account eligibility boundary."""

    def __init__(self, uow: UnitOfWorkFactory, hasher: PasswordHasher | None = None):
        self.uow = uow
        self.hasher = hasher or PASSWORD_HASHER

    def authenticate(self, email: str, password: str) -> AuthenticatedPrincipal | None:
        try:
            normalized_email = normalize_email(email)
        except (TypeError, ValidationError):
            self._dummy_verify(password)
            return None

        with self.uow() as uow:
            user = uow.users.find_by_email(normalized_email)
            if user is None or not user.is_active or user.is_deleted:
                self._dummy_verify(password)
                return None
            user_id = user.id
            stored_hash = user.password_hash
            try:
                self.hasher.verify(stored_hash, password)
            except VerifyMismatchError:
                return None
            except (InvalidHashError, VerificationError):
                self._dummy_verify(password)
                return None

            try:
                needs_rehash = self.hasher.check_needs_rehash(stored_hash)
            except InvalidHashError:
                return None
        if needs_rehash:
            replacement_hash = self.hasher.hash(password)
            try:
                with self.uow(write=True) as uow:
                    uow.users.replace_password_hash(user_id, stored_hash, replacement_hash)
            except PasswordHashReplacementError:
                return None
        return AuthenticatedPrincipal(id=user_id)

    def _dummy_verify(self, password: str) -> None:
        try:
            self.hasher.verify(_DUMMY_PASSWORD_HASH, password)
        except (InvalidHashError, VerificationError, TypeError):
            pass


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
                raise ConflictError("Team name already exists in this installation") from exc
            self._add_audit(uow, self._audit(actor.id, team.id, "created"))
            return team

    def rename_team(self, actor_id: str, team_id: str, name: str) -> TeamRecord:
        with self.uow(write=True, serialized=True) as uow:
            actor = require_admin(uow, actor_id)
            team = self._target(uow, team_id)
            team.name = normalize_team_name(name)
            team.updated_at = _now()
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                raise ConflictError("Team name already exists in this installation") from exc
            self._add_audit(uow, self._audit(actor.id, team.id, "renamed"))
            return team

    def delete_team(self, actor_id: str, team_id: str) -> None:
        with self.uow(write=True, serialized=True) as uow:
            actor = require_admin(uow, actor_id)
            team = self._target(uow, team_id, for_update=True)
            for grant in uow.grants.list_for_team(team.id, for_update=True):
                self._add_audit(uow, self._grant_audit(actor.id, grant.id))
                uow.grants.delete(grant)
            self._add_audit(uow, self._audit(actor.id, team.id, "deleted"))
            uow.teams.delete(team)

    def list_teams(self, actor_id: str) -> list[TeamRecord]:
        with self.uow() as uow:
            require_admin(uow, actor_id)
            return uow.teams.list_by_name()

    @staticmethod
    def _target(uow: UnitOfWork, team_id: str, *, for_update: bool = False) -> TeamRecord:
        team = uow.teams.get(team_id, for_update=for_update)
        if team is None:
            raise NotFoundError("Team not found")
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


class GrantService:
    """Administrator-controlled lifecycle for resource Grants."""

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
            actor = require_admin(uow, actor_id)
            self._validate_permission(permission)
            if subject_type not in SUBJECT_TYPES:
                raise ValidationError("Subject type must be user or team")
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
                raise ConflictError("Subject already has a Grant for this resource") from exc
            self._add_audit(uow, self._audit(actor.id, grant.id, "created"))
            return grant

    def change_permission(self, actor_id: str, grant_id: str, permission: str) -> GrantRecord:
        with self.uow(write=True, serialized=True) as uow:
            actor = require_admin(uow, actor_id)
            self._validate_permission(permission)
            grant = self._target(uow, grant_id, for_update=True)
            grant.permission = permission
            grant.updated_at = _now()
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
            raise ValidationError("Permission must be viewer, editor or admin") from exc

    @staticmethod
    def _require_subject(uow: UnitOfWork, subject_type: str, subject_id: str) -> None:
        if subject_type == "user":
            subject = uow.users.get(subject_id, for_update=True)
            if subject is None:
                raise NotFoundError("User not found")
            if not subject.is_active or subject.is_deleted:
                raise InvalidLifecycleError("Only active users can receive direct Grants")
            return
        if uow.teams.get(subject_id, for_update=True) is None:
            raise NotFoundError("Team not found")

    @staticmethod
    def _target(uow: UnitOfWork, grant_id: str, *, for_update: bool = False) -> GrantRecord:
        grant = uow.grants.get(grant_id, for_update=for_update)
        if grant is None:
            raise NotFoundError("Grant not found")
        return grant

    @staticmethod
    def _audit(actor_id: str, grant_id: str, action: str) -> AuditEntry:
        return AuditEntry(
            actor_user_id=actor_id,
            object_type="grant",
            object_id=grant_id,
            action=action,
        )

    _add_audit = staticmethod(UserService._add_audit)


class AuthorizationService:
    """Single resource-permission engine plus the separate Community admin rule."""

    def __init__(self, data: PermissionDataSource, resources: ResourceTypeRegistry) -> None:
        self.data = data
        self.resources = resources

    def effective_permission(
        self, user_id: str, resource_type: str, resource_id: str
    ) -> str | None:
        context = self.data.load_context(user_id, resource_type, resource_id)
        if not context.subject.exists or not context.subject.active or context.subject.deleted:
            return None
        self.resources.require_existing(resource_type, resource_id)
        return highest_permission(context.permissions)

    def check_access(
        self, user_id: str, resource_type: str, resource_id: str, required: str
    ) -> bool:
        GrantService._validate_permission(required)
        context = self.data.load_context(user_id, resource_type, resource_id)
        if context.subject.exists and context.subject.active and not context.subject.deleted:
            self.resources.require_existing(resource_type, resource_id)
        return permits(context.subject, context.permissions, required)


class MembershipService:
    """Team membership operations within the one Community installation context."""

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
                raise ConflictError("User is already a member of this team") from exc
            self._add_audit(uow, self._audit(actor.id, membership.id, "created"))
            return membership

    def delete_membership(self, actor_id: str, team_id: str, membership_id: str) -> None:
        with self.uow(write=True, serialized=True) as uow:
            actor = require_admin(uow, actor_id)
            membership = uow.memberships.get(membership_id)
            if membership is None or membership.team_id != team_id:
                raise NotFoundError("Membership not found")
            self._lock_team(uow, team_id)
            membership = uow.memberships.get(membership_id, for_update=True)
            if membership is None or membership.team_id != team_id:
                raise NotFoundError("Membership not found")
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
            raise NotFoundError("Team not found")
        return team

    @staticmethod
    def _lock_team(uow: UnitOfWork, team_id: str) -> TeamRecord:
        team = uow.teams.get(team_id, for_update=True)
        if team is None:
            raise NotFoundError("Team not found")
        return team

    @staticmethod
    def _lock_user(uow: UnitOfWork, user_id: str) -> UserRecord:
        user = uow.users.get(user_id, for_update=True)
        if user is None:
            raise NotFoundError("User not found")
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


class SessionService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        lifetime: timedelta = timedelta(hours=12),
        *,
        clock: Callable[[], datetime] = utc_now,
        retention: timedelta = timedelta(days=7),
        cleanup_batch_size: int = 100,
    ):
        self.sessions = sessions
        self.lifetime = lifetime
        self.clock = clock
        self.retention = retention
        self.cleanup_batch_size = cleanup_batch_size
        self._cleanup_cursor: str | None = None
        self._cleanup_lock = threading.Lock()

    def create(self, user: User | AuthenticatedPrincipal) -> tuple[str, LoginSession]:
        self.cleanup()
        token = secrets.token_urlsafe(48)
        login = LoginSession(
            user_id=user.id,
            token_hash=self._digest(token),
            csrf_token=secrets.token_urlsafe(32),
            expires_at=self.clock() + self.lifetime,
        )
        with self.sessions.begin() as session:
            session.add(login)
            session.flush()
            session.expunge(login)
        return token, login

    def resolve(self, token: str) -> User | None:
        resolved = self.resolve_with_session(token)
        return resolved[0] if resolved else None

    def resolve_with_session(self, token: str) -> tuple[User, LoginSession] | None:
        with self.sessions() as session:
            login = session.scalar(
                select(LoginSession).where(LoginSession.token_hash == self._digest(token))
            )
            if login is None or login.revoked_at is not None:
                return None
            expires_at = login.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= self.clock():
                return None
            user = session.get(User, login.user_id)
            if user is None or not user.is_active or user.is_deleted:
                return None
            return user, login

    def revoke(self, token: str) -> None:
        with self.sessions.begin() as session:
            session.execute(
                update(LoginSession)
                .where(LoginSession.token_hash == self._digest(token))
                .values(revoked_at=self.clock())
            )
        self.cleanup()

    def cleanup(self) -> int:
        """Inspect and delete at most one primary-key page of retained sessions."""
        with self._cleanup_lock:
            now = self.clock()
            cutoff = now - self.retention
            with self.sessions.begin() as session:
                statement = (
                    select(LoginSession.id).order_by(LoginSession.id).limit(self.cleanup_batch_size)
                )
                if self._cleanup_cursor is not None:
                    statement = statement.where(LoginSession.id > self._cleanup_cursor)
                ids = list(session.scalars(statement))
                if not ids:
                    self._cleanup_cursor = None
                    return 0
                self._cleanup_cursor = ids[-1] if len(ids) == self.cleanup_batch_size else None
                result = session.execute(
                    delete(LoginSession).where(
                        LoginSession.id.in_(ids),
                        or_(
                            LoginSession.expires_at <= cutoff,
                            LoginSession.revoked_at <= cutoff,
                        ),
                    )
                )
                return result.rowcount or 0

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

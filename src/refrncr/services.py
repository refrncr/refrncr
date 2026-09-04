from __future__ import annotations

import hashlib
import re
import secrets
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from refrncr.authorization import highest_permission, permits, validate_permission
from refrncr.models import LoginSession, User, utc_now
from refrncr.ports import (
    AuditEntry,
    DatasetBusinessKey,
    DatasetFieldRecord,
    DatasetRecord,
    GrantRecord,
    MembershipRecord,
    OrganisationRecord,
    PasswordHashReplacementError,
    PermissionDataSource,
    PersistenceConflictError,
    RecordValue,
    StoredRecord,
    TeamRecord,
    UnitOfWork,
    UnitOfWorkFactory,
    UserRecord,
)

SUBJECT_TYPES = {"user", "team"}
DATASET_FIELD_TYPES = ("string", "number", "boolean", "date")

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


class RecordValidationError(ValidationError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("Record contains invalid values")
        self.errors = errors


class RecordConflictError(ConflictError):
    def __init__(self, message: str, field_key: str | None = None) -> None:
        super().__init__(message)
        self.field_key = field_key


class StaleRecordError(ConflictError):
    """The submitted Record revision no longer matches persisted state."""


class StaleFieldOrderError(ConflictError):
    """The submitted field order no longer matches persisted state."""


_EXPECTED_REVISION_PATTERN = re.compile(r"[1-9][0-9]*")
MAX_RECORD_REVISION = 2_147_483_647


def parse_expected_revision(raw: str) -> int:
    if not isinstance(raw, str) or _EXPECTED_REVISION_PATTERN.fullmatch(raw) is None:
        raise ValidationError("Expected revision must be a canonical positive integer")
    revision = int(raw)
    if revision > MAX_RECORD_REVISION:
        raise ValidationError("Expected revision is out of range")
    return revision


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    id: str


@dataclass(frozen=True, slots=True)
class AuthorizedResourceSet:
    all_resources: bool
    resource_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DatasetDetails:
    id: str
    key: str
    name: str
    description: str
    owner_user_id: str
    owner_display_name: str


@dataclass(frozen=True, slots=True)
class RecordPage:
    records: tuple[StoredRecord, ...]
    page: int
    page_size: int
    total: int


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


def validate_dataset_key(key: str) -> str:
    if not isinstance(key, str) or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", key) is None:
        raise ValidationError(
            "Dataset key must contain 1-100 lowercase ASCII letters, numbers, "
            "underscores, or hyphens"
        )
    return key


def normalize_dataset_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValidationError("Dataset name must be text")
    normalized_name = unicodedata.normalize("NFC", name.strip())
    if not 1 <= len(normalized_name) <= 200:
        raise ValidationError("Dataset name must contain between 1 and 200 characters")
    return normalized_name


def validate_dataset_description(description: str) -> str:
    if not isinstance(description, str):
        raise ValidationError("Dataset description must be text")
    if len(description) > 10_000:
        raise ValidationError("Dataset description must not exceed 10000 characters")
    return description


def validate_dataset_field_key(key: str) -> str:
    if not isinstance(key, str) or re.fullmatch(r"[a-z0-9][a-z0-9_]{0,99}", key) is None:
        raise ValidationError(
            "Field key must contain 1-100 lowercase ASCII letters, numbers, or underscores"
        )
    return key


def normalize_dataset_field_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValidationError("Field name must be text")
    normalized_name = unicodedata.normalize("NFC", name.strip())
    if not 1 <= len(normalized_name) <= 200:
        raise ValidationError("Field name must contain between 1 and 200 characters")
    return normalized_name


def validate_dataset_field_type(data_type: str) -> str:
    if not isinstance(data_type, str) or data_type not in DATASET_FIELD_TYPES:
        raise ValidationError("Field type must be string, number, boolean or date")
    return data_type


def validate_dataset_field_flag(value: bool, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{label} must be a boolean")
    return value


_DECIMAL_PATTERN = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?")


def canonicalize_record_value(data_type: str, raw: str) -> tuple[object, bytes] | None:
    if not isinstance(raw, str):
        raise ValidationError("Value must be text")
    if data_type == "string":
        value = unicodedata.normalize("NFC", raw.strip())
        if not value:
            return None
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise ValidationError("Control characters are not allowed")
        encoded = value.encode("utf-8")
        if len(encoded) > 1000:
            raise ValidationError("Value must not exceed 1000 UTF-8 bytes")
        return value, b"s" + encoded
    if data_type == "number":
        if raw == "":
            return None
        if _DECIMAL_PATTERN.fullmatch(raw) is None:
            raise ValidationError("Enter a decimal number without exponent or separators")
        integer, _, fraction = raw.lstrip("-").partition(".")
        significant_integer = integer.lstrip("0")
        precision = len(significant_integer) + len(fraction)
        if precision > 38 or len(fraction) > 18 or len(integer) > 20:
            raise ValidationError("Number must have at most 20 integer and 18 fractional digits")
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise ValidationError("Enter a valid decimal number") from exc
        if value == 0:
            canonical = "0"
            value = Decimal(0)
        else:
            canonical = format(value, "f")
            if "." in canonical:
                canonical = canonical.rstrip("0").rstrip(".")
        return value, b"n" + canonical.encode("ascii")
    if data_type == "boolean":
        if raw == "":
            return None
        if raw not in {"true", "false"}:
            raise ValidationError("Choose true or false")
        value = raw == "true"
        return value, b"b1" if value else b"b0"
    if data_type == "date":
        if raw == "":
            return None
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw) is None:
            raise ValidationError("Enter a date as YYYY-MM-DD")
        try:
            value = date.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationError("Enter a valid Gregorian date") from exc
        return value, b"d" + raw.encode("ascii")
    raise ValidationError("Unknown field type")


def require_admin(uow: UnitOfWork, actor_id: str, *, for_update: bool = False) -> UserRecord:
    actor = uow.users.get(actor_id, for_update=for_update)
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


class DatasetResourceResolver:
    """Persistence-neutral Dataset existence resolver for resource Grants."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self.uow = uow

    def __call__(self, dataset_id: str) -> bool:
        with self.uow() as uow:
            return uow.datasets.exists(dataset_id)


class DatasetService:
    """Authorized Dataset operations within the one Community installation context."""

    def __init__(self, uow: UnitOfWorkFactory, authorization: AuthorizationService) -> None:
        self.uow = uow
        self.authorization = authorization

    def create_dataset(self, actor_id: str, key: str, name: str, description: str) -> DatasetRecord:
        with self.uow(write=True, serialized=True) as uow:
            actor = require_admin(uow, actor_id, for_update=True)
            dataset = uow.datasets.create(
                key=validate_dataset_key(key),
                name=normalize_dataset_name(name),
                description=validate_dataset_description(description),
                owner_user_id=actor.id,
            )
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                if exc.constraint == "uq_datasets_key":
                    raise ConflictError("Dataset key already exists in this installation") from exc
                raise

            grant = uow.grants.create(
                subject_type="user",
                subject_id=actor.id,
                resource_type="dataset",
                resource_id=dataset.id,
                permission="admin",
            )
            uow.flush()
            self._add_audit(uow, self._audit(actor.id, "dataset", dataset.id, "created"))
            self._add_audit(uow, self._audit(actor.id, "grant", grant.id, "created"))
            return dataset

    def list_datasets(self, actor_id: str) -> list[DatasetRecord]:
        access = self.authorization.authorized_resources(actor_id, "dataset", "viewer")
        with self.uow() as uow:
            if access.all_resources:
                return uow.datasets.list_all()
            return uow.datasets.list_by_ids(access.resource_ids)

    def get_dataset(self, actor_id: str, key: str) -> DatasetRecord:
        return self._get_dataset_with_access(actor_id, key, "viewer")

    def get_dataset_details(self, actor_id: str, key: str) -> DatasetDetails:
        dataset = self.get_dataset(actor_id, key)
        with self.uow() as uow:
            owner = uow.users.get(dataset.owner_user_id)
        return DatasetDetails(
            id=dataset.id,
            key=dataset.key,
            name=dataset.name,
            description=dataset.description,
            owner_user_id=dataset.owner_user_id,
            owner_display_name=(owner.display_name if owner is not None else dataset.owner_user_id),
        )

    def get_dataset_for_edit(self, actor_id: str, key: str) -> DatasetRecord:
        return self._get_dataset_with_access(actor_id, key, "editor")

    def _get_dataset_with_access(
        self, actor_id: str, key: str, required_permission: str
    ) -> DatasetRecord:
        dataset = self._find_by_key(key)
        if not self.authorization.check_access(
            actor_id, "dataset", dataset.id, required_permission
        ):
            raise NotFoundError("Dataset not found")
        return dataset

    def update_dataset(self, actor_id: str, key: str, name: str, description: str) -> DatasetRecord:
        validated_key = self._validated_lookup_key(key)
        normalized_name = normalize_dataset_name(name)
        validated_description = validate_dataset_description(description)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_key, for_update=True)
            if dataset is None or not self.authorization.check_access(
                actor_id, "dataset", dataset.id, "editor"
            ):
                raise NotFoundError("Dataset not found")
            dataset.name = normalized_name
            dataset.description = validated_description
            dataset.updated_at = _now()
            self._add_audit(
                uow,
                self._audit(actor_id, "dataset", dataset.id, "metadata_updated"),
            )
            return dataset

    def _find_by_key(self, key: str) -> DatasetRecord:
        validated_key = self._validated_lookup_key(key)
        with self.uow() as uow:
            dataset = uow.datasets.find_by_key(validated_key)
            if dataset is None:
                raise NotFoundError("Dataset not found")
            return dataset

    @staticmethod
    def _validated_lookup_key(key: str) -> str:
        try:
            return validate_dataset_key(key)
        except ValidationError as exc:
            raise NotFoundError("Dataset not found") from exc

    @staticmethod
    def _audit(actor_id: str, object_type: str, object_id: str, action: str) -> AuditEntry:
        return AuditEntry(
            actor_user_id=actor_id,
            object_type=object_type,
            object_id=object_id,
            action=action,
        )

    _add_audit = staticmethod(UserService._add_audit)


class DatasetFieldService:
    """Authorized schema operations scoped through a parent Dataset."""

    def __init__(self, uow: UnitOfWorkFactory, authorization: AuthorizationService) -> None:
        self.uow = uow
        self.authorization = authorization

    def create_field(
        self,
        actor_id: str,
        dataset_key: str,
        key: str,
        name: str,
        data_type: str,
        is_required: bool = False,
        is_unique: bool = False,
        is_business_key_component: bool = False,
    ) -> DatasetFieldRecord:
        validated_dataset_key = self._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_dataset_key, for_update=True)
            self._require_dataset_access(actor_id, dataset, "admin")
            validated_required = validate_dataset_field_flag(is_required, "is_required")
            validated_component = validate_dataset_field_flag(
                is_business_key_component, "is_business_key_component"
            )
            if validated_component and not validated_required:
                raise ValidationError("A Record identifier field must be required")
            if uow.dataset_records.count(dataset.id) and (
                validated_required or validated_component
            ):
                raise ValidationError("Required fields cannot be added after records exist")
            field = uow.dataset_fields.create(
                dataset_id=dataset.id,
                key=validate_dataset_field_key(key),
                name=normalize_dataset_field_name(name),
                data_type=validate_dataset_field_type(data_type),
                is_required=validated_required,
                is_unique=validate_dataset_field_flag(is_unique, "is_unique"),
                display_position=len(uow.dataset_fields.list_for_dataset(dataset.id)) + 1,
            )
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                if exc.constraint == "uq_dataset_fields_dataset_key":
                    raise ConflictError("Field key already exists in this Dataset") from exc
                raise
            self._add_audit(uow, actor_id, field.id, "created")
            if validated_component:
                current_key = uow.dataset_business_keys.get(dataset.id)
                replacement_keys = tuple(sorted((*current_key.field_keys, field.key)))
                uow.dataset_business_keys.replace(dataset.id, replacement_keys)
                uow.add_audit(
                    actor_user_id=actor_id,
                    object_type="dataset",
                    object_id=dataset.id,
                    action=(
                        "business_key_defined"
                        if not current_key.field_keys
                        else "business_key_updated"
                    ),
                )
            return field

    def list_fields(self, actor_id: str, dataset_key: str) -> list[DatasetFieldRecord]:
        dataset = self._get_dataset_with_access(actor_id, dataset_key, "viewer")
        with self.uow() as uow:
            return uow.dataset_fields.list_for_dataset(dataset.id)

    def get_field(self, actor_id: str, dataset_key: str, field_key: str) -> DatasetFieldRecord:
        dataset = self._get_dataset_with_access(actor_id, dataset_key, "viewer")
        validated_field_key = self._validated_field_lookup_key(field_key)
        with self.uow() as uow:
            field = uow.dataset_fields.find_by_key(dataset.id, validated_field_key)
            if field is None:
                raise NotFoundError("Dataset field not found")
            return field

    def update_field(
        self,
        actor_id: str,
        dataset_key: str,
        field_key: str,
        name: str,
        data_type: str,
        is_required: bool,
        is_unique: bool,
    ) -> DatasetFieldRecord:
        validated_dataset_key = self._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_dataset_key, for_update=True)
            self._require_dataset_access(actor_id, dataset, "admin")
            validated_field_key = self._validated_field_lookup_key(field_key)
            field = uow.dataset_fields.find_by_key(dataset.id, validated_field_key, for_update=True)
            if field is None:
                raise NotFoundError("Dataset field not found")

            normalized_name = normalize_dataset_field_name(name)
            validated_type = validate_dataset_field_type(data_type)
            validated_required = validate_dataset_field_flag(is_required, "is_required")
            validated_unique = validate_dataset_field_flag(is_unique, "is_unique")
            if (
                field.is_required
                and not validated_required
                and uow.dataset_business_keys.contains(dataset.id, field.key)
            ):
                raise ValidationError("A business-key field must remain required")
            replacement = (
                normalized_name,
                validated_type,
                validated_required,
                validated_unique,
            )
            current = (field.name, field.data_type, field.is_required, field.is_unique)
            if replacement == current:
                return field

            self._validate_record_aware_definition_change(
                uow, dataset.id, field, validated_type, validated_required, validated_unique
            )
            if validated_unique != field.is_unique:
                uow.dataset_records.set_field_unique_keys(dataset.id, field.id, validated_unique)

            field.name = normalized_name
            field.data_type = validated_type
            field.is_required = validated_required
            field.is_unique = validated_unique
            field.updated_at = _now()
            self._add_audit(uow, actor_id, field.id, "definition_updated")
            return field

    def update_field_and_business_key_membership(
        self,
        actor_id: str,
        dataset_key: str,
        field_key: str,
        name: str,
        data_type: str,
        is_required: bool,
        is_unique: bool,
        is_business_key_component: bool,
    ) -> DatasetFieldRecord:
        validated_dataset_key = self._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_dataset_key, for_update=True)
            self._require_dataset_access(actor_id, dataset, "admin")
            validated_field_key = self._validated_field_lookup_key(field_key)
            field = uow.dataset_fields.find_by_key(dataset.id, validated_field_key, for_update=True)
            if field is None:
                raise NotFoundError("Dataset field not found")

            normalized_name = normalize_dataset_field_name(name)
            validated_type = validate_dataset_field_type(data_type)
            validated_required = validate_dataset_field_flag(is_required, "is_required")
            validated_unique = validate_dataset_field_flag(is_unique, "is_unique")
            validated_component = validate_dataset_field_flag(
                is_business_key_component, "is_business_key_component"
            )
            if validated_component and not validated_required:
                raise ValidationError("A Record identifier field must be required")

            current_key = uow.dataset_business_keys.get(dataset.id)
            currently_component = field.key in current_key.field_keys
            membership_changed = currently_component != validated_component
            replacement = (
                normalized_name,
                validated_type,
                validated_required,
                validated_unique,
            )
            current_definition = (field.name, field.data_type, field.is_required, field.is_unique)
            definition_changed = replacement != current_definition
            if not membership_changed and not definition_changed:
                return field

            if membership_changed and uow.dataset_records.count(dataset.id):
                raise ValidationError("Record identifier cannot change after records exist")
            if definition_changed:
                self._validate_record_aware_definition_change(
                    uow, dataset.id, field, validated_type, validated_required, validated_unique
                )
                if validated_unique != field.is_unique:
                    uow.dataset_records.set_field_unique_keys(
                        dataset.id, field.id, validated_unique
                    )

            if membership_changed:
                replacement_keys = set(current_key.field_keys)
                if validated_component:
                    replacement_keys.add(field.key)
                else:
                    replacement_keys.discard(field.key)
                uow.dataset_business_keys.replace(dataset.id, tuple(sorted(replacement_keys)))
                uow.add_audit(
                    actor_user_id=actor_id,
                    object_type="dataset",
                    object_id=dataset.id,
                    action=(
                        "business_key_defined"
                        if not current_key.field_keys
                        else "business_key_updated"
                    ),
                )

            if definition_changed:
                field.name = normalized_name
                field.data_type = validated_type
                field.is_required = validated_required
                field.is_unique = validated_unique
                field.updated_at = _now()
                self._add_audit(uow, actor_id, field.id, "definition_updated")
            return field

    def delete_field(self, actor_id: str, dataset_key: str, field_key: str) -> None:
        validated_dataset_key = self._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_dataset_key, for_update=True)
            self._require_dataset_access(actor_id, dataset, "admin")
            validated_field_key = self._validated_field_lookup_key(field_key)
            field = uow.dataset_fields.find_by_key(dataset.id, validated_field_key, for_update=True)
            if field is None:
                raise NotFoundError("Dataset field not found")
            if uow.dataset_business_keys.contains(dataset.id, field.key):
                raise ValidationError(
                    "Remove this field from the Record identifier before deleting it"
                )
            if uow.dataset_records.field_has_values(dataset.id, field.id):
                raise ValidationError("A field with stored values cannot be deleted")
            field_id = field.id
            uow.dataset_fields.delete(field)
            uow.flush()
            remaining = uow.dataset_fields.list_for_dataset(dataset.id)
            uow.dataset_fields.replace_order(dataset.id, tuple(item.id for item in remaining))
            self._add_audit(uow, actor_id, field_id, "deleted")

    def replace_order(
        self,
        actor_id: str,
        dataset_key: str,
        expected_order: tuple[str, ...],
        proposed_order: tuple[str, ...],
    ) -> tuple[DatasetFieldRecord, ...]:
        validated_dataset_key = self._validated_dataset_lookup_key(dataset_key)
        if not isinstance(expected_order, tuple) or not isinstance(proposed_order, tuple):
            raise ValidationError("Field order must be a complete sequence")
        if any(
            not isinstance(value, str) or not value for value in (*expected_order, *proposed_order)
        ):
            raise ValidationError("Field order contains a malformed field identity")
        if len(set(expected_order)) != len(expected_order) or len(set(proposed_order)) != len(
            proposed_order
        ):
            raise ValidationError("Field order contains duplicate fields")
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_dataset_key, for_update=True)
            self._require_dataset_access(actor_id, dataset, "admin")
            fields = uow.dataset_fields.list_for_dataset(dataset.id)
            current = tuple(field.id for field in fields)
            if expected_order != current:
                raise StaleFieldOrderError("Field order changed since this page was opened")
            if len(proposed_order) != len(current) or set(proposed_order) != set(current):
                raise ValidationError("Field order must contain every current field exactly once")
            if proposed_order == current:
                return tuple(fields)
            uow.dataset_fields.replace_order(dataset.id, proposed_order)
            uow.add_audit(
                actor_user_id=actor_id,
                object_type="dataset",
                object_id=dataset.id,
                action="field_order_updated",
            )
            by_id = {field.id: field for field in fields}
            return tuple(by_id[field_id] for field_id in proposed_order)

    def move_field(
        self,
        actor_id: str,
        dataset_key: str,
        field_id: str,
        direction: str,
        expected_order: tuple[str, ...],
    ) -> tuple[DatasetFieldRecord, ...]:
        if direction not in {"up", "down"}:
            raise ValidationError("Field direction is invalid")
        if not isinstance(field_id, str) or not field_id:
            raise NotFoundError("Dataset field not found")
        if field_id not in expected_order:
            raise NotFoundError("Dataset field not found")
        proposed = list(expected_order)
        index = proposed.index(field_id)
        target = index - 1 if direction == "up" else index + 1
        if target < 0 or target >= len(proposed):
            return self.replace_order(actor_id, dataset_key, expected_order, expected_order)
        proposed[index], proposed[target] = proposed[target], proposed[index]
        return self.replace_order(actor_id, dataset_key, expected_order, tuple(proposed))

    def _get_dataset_with_access(
        self, actor_id: str, dataset_key: str, required_permission: str
    ) -> DatasetRecord:
        validated_key = self._validated_dataset_lookup_key(dataset_key)
        with self.uow() as uow:
            dataset = uow.datasets.find_by_key(validated_key)
        self._require_dataset_access(actor_id, dataset, required_permission)
        return dataset

    def _require_dataset_access(
        self, actor_id: str, dataset: DatasetRecord | None, required_permission: str
    ) -> None:
        if dataset is None or not self.authorization.check_access(
            actor_id, "dataset", dataset.id, required_permission
        ):
            raise NotFoundError("Dataset not found")

    @staticmethod
    def _validated_dataset_lookup_key(key: str) -> str:
        try:
            return validate_dataset_key(key)
        except ValidationError as exc:
            raise NotFoundError("Dataset not found") from exc

    @staticmethod
    def _validated_field_lookup_key(key: str) -> str:
        try:
            return validate_dataset_field_key(key)
        except ValidationError as exc:
            raise NotFoundError("Dataset field not found") from exc

    @staticmethod
    def _add_audit(uow: UnitOfWork, actor_id: str, field_id: str, action: str) -> None:
        uow.add_audit(
            actor_user_id=actor_id,
            object_type="dataset_field",
            object_id=field_id,
            action=action,
        )

    @staticmethod
    def _validate_record_aware_definition_change(
        uow: UnitOfWork,
        dataset_id: str,
        field: DatasetFieldRecord,
        data_type: str,
        is_required: bool,
        is_unique: bool,
    ) -> None:
        has_records = bool(uow.dataset_records.count(dataset_id))
        if has_records and data_type != field.data_type:
            raise ValidationError("Field type cannot change after records exist")
        if (
            not field.is_required
            and is_required
            and uow.dataset_records.field_has_missing_values(dataset_id, field.id)
        ):
            raise ValidationError("Required cannot be enabled while records have no value")
        if (
            not field.is_unique
            and is_unique
            and uow.dataset_records.field_has_duplicate_values(dataset_id, field.id)
        ):
            raise ValidationError("Unique cannot be enabled while duplicate values exist")


class DatasetBusinessKeyService:
    """Authorized business-key metadata operations scoped through a parent Dataset."""

    def __init__(self, uow: UnitOfWorkFactory, authorization: AuthorizationService) -> None:
        self.uow = uow
        self.authorization = authorization

    def get_business_key(self, actor_id: str, dataset_key: str) -> DatasetBusinessKey:
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow() as uow:
            dataset = uow.datasets.find_by_key(validated_key)
            self._require_dataset_access(actor_id, dataset, "viewer")
            return uow.dataset_business_keys.get(dataset.id)

    def set_business_key(
        self, actor_id: str, dataset_key: str, field_keys: tuple[str, ...]
    ) -> DatasetBusinessKey:
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_key, for_update=True)
            self._require_dataset_access(actor_id, dataset, "admin")
            if not isinstance(field_keys, tuple) or not field_keys:
                raise ValidationError("At least one business-key field is required")
            if any(not isinstance(field_key, str) for field_key in field_keys):
                raise ValidationError("Business-key field keys must be text")
            if len(set(field_keys)) != len(field_keys):
                raise ValidationError("Business-key fields must not contain duplicates")

            components: list[str] = []
            for submitted_key in field_keys:
                try:
                    field_key = validate_dataset_field_key(submitted_key)
                except ValidationError as exc:
                    raise ValidationError("Selected Dataset field does not exist") from exc
                field = uow.dataset_fields.find_by_key(dataset.id, field_key)
                if field is None:
                    raise ValidationError("Selected Dataset field does not exist")
                if not field.is_required:
                    raise ValidationError("Every business-key field must be required")
                components.append(field.key)

            canonical = tuple(sorted(components))
            current = uow.dataset_business_keys.get(dataset.id)
            if current.field_keys == canonical:
                return current

            if uow.dataset_records.count(dataset.id):
                raise ValidationError("Record identifier cannot change after records exist")

            uow.dataset_business_keys.replace(dataset.id, canonical)
            uow.add_audit(
                actor_user_id=actor_id,
                object_type="dataset",
                object_id=dataset.id,
                action=(
                    "business_key_defined" if not current.field_keys else "business_key_updated"
                ),
            )
            return DatasetBusinessKey(dataset_id=dataset.id, field_keys=canonical)

    def _require_dataset_access(
        self, actor_id: str, dataset: DatasetRecord | None, required_permission: str
    ) -> None:
        if dataset is None or not self.authorization.check_access(
            actor_id, "dataset", dataset.id, required_permission
        ):
            raise NotFoundError("Dataset not found")


class DatasetRecordService:
    """Authorized current-record operations scoped through a parent Dataset."""

    PAGE_SIZE = 50

    def __init__(self, uow: UnitOfWorkFactory, authorization: AuthorizationService) -> None:
        self.uow = uow
        self.authorization = authorization

    def create_record(
        self, actor_id: str, dataset_key: str, submitted: dict[str, str]
    ) -> StoredRecord:
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_key, for_update=True)
            self._require_access(actor_id, dataset, "editor")
            fields = uow.dataset_fields.list_for_dataset(dataset.id)
            identifier = uow.dataset_business_keys.get(dataset.id)
            values, encoded_identifier = self._validated_values(fields, identifier, submitted)
            record = uow.dataset_records.create(
                dataset_id=dataset.id, identifier_key=encoded_identifier
            )
            try:
                uow.flush()
                uow.dataset_records.add_values(record, tuple(values))
                uow.flush()
            except PersistenceConflictError as error:
                if error.constraint == "uq_dataset_records_dataset_identifier":
                    raise RecordConflictError("Record identifier already exists") from error
                if error.constraint == "uq_dataset_record_values_unique_value":
                    raise RecordConflictError("A unique field value already exists") from error
                raise
            uow.add_audit(
                actor_user_id=actor_id,
                object_type="dataset_record",
                object_id=record.id,
                action="created",
            )
            return StoredRecord(record.id, dataset.id, tuple(values), 1)

    def get_record_for_edit(self, actor_id: str, dataset_key: str, record_id: str) -> StoredRecord:
        dataset = self._find_dataset(actor_id, dataset_key, "editor")
        with self.uow() as uow:
            record = uow.dataset_records.get(dataset.id, record_id)
        if record is None:
            raise NotFoundError("Record not found")
        return record

    def update_record(
        self,
        actor_id: str,
        dataset_key: str,
        record_id: str,
        expected_revision: int,
        submitted: dict[str, str],
    ) -> StoredRecord:
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ValidationError("Expected revision must be a positive integer")
        if not 1 <= expected_revision <= MAX_RECORD_REVISION:
            raise ValidationError("Expected revision is out of range")
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_key, for_update=True)
            self._require_access(actor_id, dataset, "editor")
            existing = uow.dataset_records.get(dataset.id, record_id)
            if existing is None:
                raise NotFoundError("Record not found")
            fields = uow.dataset_fields.list_for_dataset(dataset.id)
            identifier = uow.dataset_business_keys.get(dataset.id)
            values, encoded_identifier = self._validated_values(fields, identifier, submitted)
            try:
                updated = uow.dataset_records.update_if_revision(
                    dataset.id, record_id, expected_revision, encoded_identifier
                )
                if not updated:
                    self._raise_missing_or_stale(uow, dataset.id, record_id)
                uow.dataset_records.replace_values(existing, values)
                uow.flush()
            except PersistenceConflictError as error:
                self._raise_record_conflict(error)
            uow.add_audit(
                actor_user_id=actor_id,
                object_type="dataset_record",
                object_id=record_id,
                action="updated",
            )
            return StoredRecord(record_id, dataset.id, values, expected_revision + 1)

    def delete_record(
        self, actor_id: str, dataset_key: str, record_id: str, expected_revision: int
    ) -> None:
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ValidationError("Expected revision must be a positive integer")
        if not 1 <= expected_revision <= MAX_RECORD_REVISION:
            raise ValidationError("Expected revision is out of range")
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_key, for_update=True)
            self._require_access(actor_id, dataset, "editor")
            if not uow.dataset_records.delete_if_revision(dataset.id, record_id, expected_revision):
                self._raise_missing_or_stale(uow, dataset.id, record_id)
            uow.add_audit(
                actor_user_id=actor_id,
                object_type="dataset_record",
                object_id=record_id,
                action="deleted",
            )

    @staticmethod
    def _raise_missing_or_stale(uow: UnitOfWork, dataset_id: str, record_id: str) -> None:
        if uow.dataset_records.current_revision(dataset_id, record_id) is None:
            raise NotFoundError("Record not found")
        raise StaleRecordError("Record changed since this form was opened")

    @staticmethod
    def _raise_record_conflict(error: PersistenceConflictError) -> None:
        if error.constraint == "uq_dataset_records_dataset_identifier":
            raise RecordConflictError("Record identifier already exists") from error
        if error.constraint == "uq_dataset_record_values_unique_value":
            raise RecordConflictError("A unique field value already exists") from error
        raise error

    @staticmethod
    def _validated_values(fields, identifier, submitted):
        field_by_key = {field.key: field for field in fields}
        unknown = sorted(set(submitted) - set(field_by_key))
        if unknown:
            raise RecordValidationError({key: "Unknown Dataset field" for key in unknown})
        if not identifier.field_keys:
            raise ValidationError("Configure a Record identifier before adding records")
        errors: dict[str, str] = {}
        values: list[RecordValue] = []
        for field in fields:
            raw = submitted.get(field.key, "")
            try:
                canonical = canonicalize_record_value(field.data_type, raw)
            except ValidationError as error:
                errors[field.key] = str(error)
                continue
            if canonical is None:
                if field.is_required or field.key in identifier.field_keys:
                    errors[field.key] = "A value is required"
                continue
            value, key = canonical
            values.append(
                RecordValue(field.id, field.key, field.data_type, value, key, field.is_unique)
            )
        if errors:
            raise RecordValidationError(errors)
        value_by_key = {value.field_key: value for value in values}
        components = [value_by_key[key].canonical_key for key in identifier.field_keys]
        encoded_identifier = b"".join(
            len(component).to_bytes(4, "big") + component for component in components
        )
        if len(encoded_identifier) > 2000:
            raise ValidationError("Encoded Record identifier must not exceed 2000 UTF-8 bytes")
        return tuple(values), encoded_identifier

    def list_records(self, actor_id: str, dataset_key: str, page: int = 1) -> RecordPage:
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ValidationError("Page must be a positive integer")
        dataset = self._find_dataset(actor_id, dataset_key, "viewer")
        identifier = self._business_key(dataset.id)
        with self.uow() as uow:
            records = uow.dataset_records.list_all(dataset.id)
        records.sort(key=lambda record: self._record_sort_key(record, identifier.field_keys))
        start = (page - 1) * self.PAGE_SIZE
        return RecordPage(
            tuple(records[start : start + self.PAGE_SIZE]),
            page,
            self.PAGE_SIZE,
            len(records),
        )

    def export_dataset(self, actor_id: str, dataset_key: str, format_name: str):
        from refrncr.dataset_export import (
            ExportGenerationError,
            externally_ordered_records,
            generate_export,
        )

        if not isinstance(format_name, str) or format_name not in {"csv", "xlsx", "json"}:
            raise ExportGenerationError("Choose exactly one supported export format")
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow() as uow:
            uow.begin_export_snapshot()
            dataset = uow.datasets.find_by_key(validated_key)
            self._require_access(actor_id, dataset, "viewer")
            fields = tuple(uow.dataset_fields.list_for_dataset(dataset.id))
            identifier = uow.dataset_business_keys.get(dataset.id)
            records = externally_ordered_records(
                uow.dataset_records.iterate_all(dataset.id),
                lambda record: self._record_sort_key(record, identifier.field_keys),
            )
            return generate_export(format_name, fields, records, dataset.key)

    @staticmethod
    def _record_sort_key(record: StoredRecord, identifier_keys: tuple[str, ...]):
        by_key = {value.field_key: value for value in record.values}
        incomplete = any(key not in by_key for key in identifier_keys)
        result = []
        for key in identifier_keys:
            value = by_key.get(key)
            if value is None:
                result.append((1, None))
                continue
            result.append(
                (
                    0,
                    value.value.encode("utf-8") if value.data_type == "string" else value.value,
                )
            )
        return incomplete, tuple(result), record.id

    def get_dataset_for_add(self, actor_id: str, dataset_key: str) -> DatasetRecord:
        return self._find_dataset(actor_id, dataset_key, "editor")

    def _find_dataset(self, actor_id: str, key: str, permission: str) -> DatasetRecord:
        validated = DatasetFieldService._validated_dataset_lookup_key(key)
        with self.uow() as uow:
            dataset = uow.datasets.find_by_key(validated)
        self._require_access(actor_id, dataset, permission)
        return dataset

    def _business_key(self, dataset_id: str) -> DatasetBusinessKey:
        with self.uow() as uow:
            return uow.dataset_business_keys.get(dataset_id)

    def _require_access(
        self, actor_id: str, dataset: DatasetRecord | None, permission: str
    ) -> None:
        if dataset is None or not self.authorization.check_access(
            actor_id, "dataset", dataset.id, permission
        ):
            raise NotFoundError("Dataset not found")


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

    def authorized_resources(
        self, user_id: str, resource_type: str, required: str
    ) -> AuthorizedResourceSet:
        GrantService._validate_permission(required)
        context = self.data.load_resource_permissions(user_id, resource_type)
        subject = context.subject
        if not subject.exists or not subject.active or subject.deleted:
            raise AuthorizationError("Active user access required")
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

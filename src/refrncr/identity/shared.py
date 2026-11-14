"""Shared identity-domain values and validation helpers."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.low_level import Type

from refrncr.identity.errors import IdentityAuthorizationError, IdentityValidationError
from refrncr.ports import UnitOfWork, UserRecord

THEME_PREFERENCES = frozenset(("system", "light", "dark"))

PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "U29tZVN0YXRpY1NhbHQxNg$"
    "JvPgxylqfMveFSbsSWHHBEKbGOlnkLIjFBG0s33wtgE"
)


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    id: str


def validate_password(password: str) -> str:
    """Validate without trimming, normalizing, or otherwise rewriting a password."""
    if not isinstance(password, str):
        raise IdentityValidationError("Password must be text", field="password")
    if len(password) < 12:
        raise IdentityValidationError(
            "Password must contain at least 12 characters", field="password"
        )
    try:
        encoded_size = len(password.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise IdentityValidationError(
            "Password must be valid Unicode text", field="password"
        ) from exc
    if encoded_size > 1024:
        raise IdentityValidationError("Password must not exceed 1024 UTF-8 bytes", field="password")
    return password


def normalize_email(email: str) -> str:
    normalized = email.strip().casefold()
    if (
        len(normalized) > 320
        or normalized.count("@") != 1
        or any(character.isspace() or ord(character) < 32 for character in normalized)
    ):
        raise IdentityValidationError("A valid email address is required", field="email")
    local_part, domain = normalized.split("@")
    if not local_part or not domain:
        raise IdentityValidationError("A valid email address is required", field="email")
    return normalized


def normalize_team_name(name: str) -> str:
    normalized_name = unicodedata.normalize("NFC", name.strip())
    if not 1 <= len(normalized_name) <= 100:
        raise IdentityValidationError("Team name must contain between 1 and 100 characters")
    return normalized_name


def require_admin(uow: UnitOfWork, actor_id: str, *, for_update: bool = False) -> UserRecord:
    actor = uow.users.get(actor_id, for_update=for_update)
    if actor is None or actor.role != "admin" or not actor.is_active or actor.is_deleted:
        raise IdentityAuthorizationError("Administrator access required")
    return actor

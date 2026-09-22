"""User-owned API credentials; framework-independent lifecycle and authentication."""

import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from refrncr.audit import record_audit
from refrncr.audit_presentation import valid_id
from refrncr.services import (
    AuthenticatedPrincipal,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)

LIFETIME = timedelta(days=90)
LAST_USED_INTERVAL = timedelta(minutes=15)
TOKEN = re.compile(r"Bearer (rfr_([0-9a-f]{32})\.([A-Za-z0-9_-]{43}))", re.IGNORECASE | re.ASCII)


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class KeyView:
    id: str
    name: str
    prefix: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None
    revision: int
    status: str


@dataclass(frozen=True, slots=True)
class CreatedKey:
    key: KeyView
    secret: str = field(repr=False)


class ApiKeyAuthenticationError(Exception):
    """Every credential rejection has the same public contract."""

    status_code = 401
    error_code = "invalid_api_credentials"

    @property
    def body(self):
        return {"error": self.error_code}

    def __init__(self):
        super().__init__("Invalid API credentials")


class ApiKeyService:
    def __init__(self, uow, *, pepper, clock=None):
        if not isinstance(pepper, bytes) or len(pepper) < 32:
            raise ValueError(
                "API key verification requires a configured secret of at least 32 bytes"
            )
        self.uow = uow
        self._pepper = pepper
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _actor(work, actor_id, *, lock=False):
        actor = work.users.get(actor_id, for_update=lock) if valid_id(actor_id) else None
        if actor is None or not actor.is_active or actor.is_deleted:
            raise AuthorizationError("Active user access required")
        return actor

    @staticmethod
    def _view(key, now):
        status = (
            "Revoked" if key.revoked_at else "Expired" if utc(key.expires_at) <= now else "Active"
        )
        return KeyView(
            key.id,
            key.name,
            "rfr_" + key.id.replace("-", ""),
            utc(key.created_at),
            utc(key.expires_at),
            utc(key.revoked_at) if key.revoked_at else None,
            utc(key.last_used_at) if key.last_used_at else None,
            key.revision,
            status,
        )

    def list_keys(self, actor_id):
        with self.uow() as work:
            self._actor(work, actor_id)
            now = self._clock()
            return tuple(self._view(key, now) for key in work.api_keys.list_owned(actor_id))

    def get_key(self, actor_id, key_id):
        with self.uow() as work:
            self._actor(work, actor_id)
            key = work.api_keys.get_owned(actor_id, key_id) if valid_id(key_id) else None
            if key is None:
                raise NotFoundError("API key not found")
            return self._view(key, self._clock())

    def create_key(self, actor_id, name, expires_at=None, *, request_id=None):
        with self.uow(write=True, serialized=True, translate_errors=True) as work:
            self._actor(work, actor_id, lock=True)
            now = self._clock()
            if request_id is not None:
                if not valid_id(request_id):
                    raise ValidationError("Invalid creation request")
                if work.api_keys.get_owned(actor_id, request_id) is not None:
                    raise ConflictError(
                        "This creation form was already used. The secret cannot be shown again."
                    )
            if not isinstance(name, str) or len(name) > 100 or not name.strip():
                raise ValidationError("Enter a name of 1 to 100 characters")
            name = name.strip()
            expiry = now + LIFETIME if expires_at is None else expires_at
            if (
                not isinstance(expiry, datetime)
                or expiry.utcoffset() is None
                or not now < expiry <= now + LIFETIME
            ):
                raise ValidationError("Expiry must be in the future and within 90 days")
            active = work.api_keys.active_owned(actor_id, now)
            if len(active) >= 5:
                raise ConflictError("You can have at most five active API keys")
            if any(key.name.casefold() == name.casefold() for key in active):
                raise ConflictError("An active API key already uses this name")
            key_id = request_id or str(uuid4())
            secret = "rfr_" + key_id.replace("-", "") + "." + secrets.token_urlsafe(32)
            key = work.api_keys.create(
                id=key_id,
                owner_id=actor_id,
                name=name,
                verifier=self._digest(secret),
                created_at=now,
                expires_at=expiry.astimezone(UTC),
                revision=1,
            )
            work.flush()
            self._audit(work, actor_id, key.id, "api_key.created")
            result = CreatedKey(self._view(key, now), secret)
        return result

    def revoke_key(self, actor_id, key_id, expected_revision, *, confirmed=False):
        with self.uow(write=True, serialized=True, translate_errors=True) as work:
            self._actor(work, actor_id, lock=True)
            key = work.api_keys.get_owned(actor_id, key_id) if valid_id(key_id) else None
            if key is None:
                raise NotFoundError("API key not found")
            if confirmed is not True:
                raise ValidationError("Confirm permanent revocation")
            if type(expected_revision) is not int or expected_revision != key.revision:
                raise ConflictError("API key changed. Reload and try again")
            if key.revoked_at is not None:
                raise ConflictError("API key is already revoked")
            key.revoked_at = self._clock()
            key.revision += 1
            self._audit(work, actor_id, key.id, "api_key.revoked")

    def authenticate(self, bearer_value):
        # Only this header value boundary may establish an API actor. No session,
        # cookie, query or pre-resolved permission object is accepted.
        match = (
            TOKEN.fullmatch(bearer_value)
            if isinstance(bearer_value, str) and len(bearer_value) == 87
            else None
        )
        key_id = str(UUID(match[2])) if match else None
        principal = None
        with self.uow(write=True, serialized=True, translate_errors=True) as work:
            candidate = work.api_keys.get(key_id, for_update=True) if key_id else None
            if candidate is not None:
                actor = work.users.get(candidate.owner_id)
            else:
                actor = None
            digest = self._digest(match[1] if match else "invalid")
            verifier = candidate.verifier if candidate else ""
            verifier_valid = isinstance(verifier, str) and re.fullmatch(r"[0-9a-f]{64}", verifier)
            matches = hmac.compare_digest(digest, verifier if verifier_valid else "0" * 64)
            matches = bool(matches and verifier_valid)
            now = self._clock()
            if (
                matches
                and candidate is not None
                and candidate.revoked_at is None
                and utc(candidate.expires_at) > now
                and actor is not None
                and actor.is_active
                and not actor.is_deleted
            ):
                work.api_keys.update_last_used(
                    candidate.id,
                    used_at=now,
                    cutoff=now - LAST_USED_INTERVAL,
                )
                principal = AuthenticatedPrincipal(actor.id)
            else:
                # Untrusted input never supplies actor or object identity. This UUID
                # identifies the attempt, not a claimed key or a fabricated user.
                self._audit(work, None, str(uuid4()), "api_key.authentication_failed")
        if principal is None:
            raise ApiKeyAuthenticationError()
        return principal

    def _digest(self, secret):
        return hmac.digest(
            self._pepper, b"refrncr.api-key.v1\0" + secret.encode("ascii"), "sha256"
        ).hex()

    @staticmethod
    def _audit(work, actor_id, object_id, action):
        record_audit(
            work, actor_user_id=actor_id, object_type="api_key", object_id=object_id, action=action
        )


def configured_api_keys(uow):
    """Missing configuration disables credentials; never substitute an ephemeral pepper."""
    import os

    value = os.environ.get("REFRNCR_API_KEY_PEPPER")
    if value is None:
        return None
    if re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError("REFRNCR_API_KEY_PEPPER must contain 64 hexadecimal characters")
    return ApiKeyService(uow, pepper=bytes.fromhex(value))

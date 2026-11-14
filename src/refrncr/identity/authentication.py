"""Identity authentication service."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from refrncr.errors import ValidationError
from refrncr.identity.shared import (
    _DUMMY_PASSWORD_HASH,
    PASSWORD_HASHER,
    AuthenticatedPrincipal,
    normalize_email,
)
from refrncr.ports import PasswordHashReplacementError, UnitOfWorkFactory


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

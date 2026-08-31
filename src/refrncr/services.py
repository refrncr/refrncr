from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from argon2.low_level import Type
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from refrncr.models import AuditEvent, LoginSession, Organisation, User, utc_now

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


@contextmanager
def _write_transaction(
    sessions: sessionmaker[Session], *, immediate_sqlite: bool = False
) -> Iterator[Session]:
    session = sessions()
    try:
        if immediate_sqlite and session.get_bind().dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        else:
            session.begin()
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class UserService:
    """User operations within the one Community installation context."""

    def __init__(self, sessions: sessionmaker[Session], hasher: PasswordHasher | None = None):
        self.sessions = sessions
        self.hasher = hasher or PASSWORD_HASHER

    def bootstrap_organisation(self) -> Organisation:
        try:
            with _write_transaction(self.sessions, immediate_sqlite=True) as session:
                organisation = session.get(Organisation, "community")
                if organisation is None:
                    organisation = Organisation()
                    session.add(organisation)
                    session.flush()
                return organisation
        except IntegrityError:
            # Another process may have inserted the singleton between our read and write.
            with self.sessions() as session:
                organisation = session.get(Organisation, "community")
                if organisation is None:
                    raise
                return organisation

    def create_initial_admin(self, email: str, display_name: str, password: str) -> User:
        self.bootstrap_organisation()
        with _write_transaction(self.sessions, immediate_sqlite=True) as session:
            self._lock_organisation(session)
            if session.scalar(select(func.count(User.id))) != 0:
                raise ConflictError("Initial setup has already been completed")
            user = self._new_user(email, display_name, password, "admin")
            session.add(user)
            session.flush()
            session.add(self._audit(user.id, user, "created"))
            return user

    def create_user(
        self,
        actor_id: str,
        email: str,
        display_name: str,
        password: str,
        role: str = "user",
    ) -> User:
        with self.sessions.begin() as session:
            actor = self._require_admin(session, actor_id)
            user = self._new_user(email, display_name, password, role)
            session.add(user)
            try:
                session.flush()
            except IntegrityError as exc:
                raise ConflictError("Email already exists in this installation") from exc
            session.add(self._audit(actor.id, user, "created"))
            return user

    def change_email(self, actor_id: str, target_id: str, email: str) -> User:
        with self.sessions.begin() as session:
            actor = self._require_admin(session, actor_id)
            target = self._target(session, target_id)
            target.email = normalize_email(email)
            try:
                session.flush()
            except IntegrityError as exc:
                raise ConflictError("Email already exists in this installation") from exc
            session.add(self._audit(actor.id, target, "email_changed"))
            return target

    def disable_user(self, actor_id: str, target_id: str) -> User:
        return self._set_state(actor_id, target_id, "disabled")

    def enable_user(self, actor_id: str, target_id: str) -> User:
        return self._set_state(actor_id, target_id, "enabled")

    def delete_user(self, actor_id: str, target_id: str) -> User:
        return self._set_state(actor_id, target_id, "deleted")

    def list_users(self, actor_id: str) -> list[User]:
        with self.sessions() as session:
            self._require_admin(session, actor_id)
            return list(session.scalars(select(User).order_by(User.email)))

    def authenticate(self, email: str, password: str) -> User | None:
        try:
            normalized_email = normalize_email(email)
        except ValidationError:
            return None
        with self.sessions() as session:
            user = session.scalar(select(User).where(User.email == normalized_email))
            if user is None or not user.is_active or user.is_deleted:
                return None
            try:
                self.hasher.verify(user.password_hash, password)
            except VerifyMismatchError:
                return None
            return user

    def _set_state(self, actor_id: str, target_id: str, action: str) -> User:
        with _write_transaction(self.sessions, immediate_sqlite=True) as session:
            self._lock_organisation(session)
            actor = self._require_admin(session, actor_id)
            target = self._target(session, target_id)
            if action == "enabled":
                if target.is_deleted:
                    raise InvalidLifecycleError("Deleted users cannot be enabled")
                target.is_active = True
            else:
                if target.role == "admin" and target.is_active and not target.is_deleted:
                    active_admins = session.scalar(
                        select(func.count(User.id)).where(
                            User.role == "admin",
                            User.is_active.is_(True),
                            User.is_deleted.is_(False),
                        )
                    )
                    if active_admins == 1:
                        raise InvalidLifecycleError(
                            "The last active administrator cannot be removed"
                        )
                target.is_active = False
                if action == "deleted":
                    target.is_deleted = True
                session.execute(
                    update(LoginSession)
                    .where(LoginSession.user_id == target.id, LoginSession.revoked_at.is_(None))
                    .values(revoked_at=utc_now())
                )
            target.updated_at = utc_now()
            session.add(self._audit(actor.id, target, action))
            return target

    def _new_user(self, email: str, display_name: str, password: str, role: str) -> User:
        if role not in {"admin", "user"}:
            raise ValidationError("Unknown role")
        if not display_name.strip():
            raise ValidationError("Display name is required")
        return User(
            email=normalize_email(email),
            display_name=display_name.strip(),
            password_hash=self.hasher.hash(password),
            role=role,
            is_active=True,
            is_deleted=False,
        )

    @staticmethod
    def _require_admin(session: Session, actor_id: str) -> User:
        actor = session.get(User, actor_id)
        if actor is None or actor.role != "admin" or not actor.is_active or actor.is_deleted:
            raise AuthorizationError("Administrator access required")
        return actor

    @staticmethod
    def _target(session: Session, target_id: str) -> User:
        target = session.get(User, target_id)
        if target is None:
            raise NotFoundError("User not found")
        return target

    @staticmethod
    def _lock_organisation(session: Session) -> Organisation:
        statement = select(Organisation).where(Organisation.id == "community")
        if session.get_bind().dialect.name != "sqlite":
            statement = statement.with_for_update()
        organisation = session.scalar(statement)
        if organisation is None:
            raise ConflictError("Community Organisation has not been bootstrapped")
        return organisation

    @staticmethod
    def _audit(actor_id: str, target: User, action: str) -> AuditEvent:
        return AuditEvent(
            actor_user_id=actor_id,
            object_type="user",
            object_id=target.id,
            action=action,
        )


class SessionService:
    def __init__(self, sessions: sessionmaker[Session], lifetime: timedelta = timedelta(hours=12)):
        self.sessions = sessions
        self.lifetime = lifetime

    def create(self, user: User) -> tuple[str, LoginSession]:
        token = secrets.token_urlsafe(48)
        login = LoginSession(
            user_id=user.id,
            token_hash=self._digest(token),
            csrf_token=secrets.token_urlsafe(32),
            expires_at=utc_now() + self.lifetime,
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
                expires_at = expires_at.replace(tzinfo=utc_now().tzinfo)
            if expires_at <= utc_now():
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
                .values(revoked_at=utc_now())
            )

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

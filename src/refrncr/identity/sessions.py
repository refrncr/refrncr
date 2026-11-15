"""Identity browser-session service."""

import hashlib
import secrets
import threading
from datetime import timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from refrncr.clock import SYSTEM_CLOCK, Clock
from refrncr.identity.shared import AuthenticatedPrincipal
from refrncr.models import LoginSession, User


class SessionService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        lifetime: timedelta = timedelta(hours=12),
        *,
        clock: Clock = SYSTEM_CLOCK,
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
        now = self.clock.now()
        login = LoginSession(
            user_id=user.id,
            token_hash=self._digest(token),
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            expires_at=now + self.lifetime,
        )
        with self.sessions.begin() as session:
            session.info["clock"] = self.clock
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
            if login.expires_at <= self.clock.now():
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
                .values(revoked_at=self.clock.now())
            )
        self.cleanup()

    def cleanup(self) -> int:
        """Inspect and delete at most one primary-key page of retained sessions."""
        with self._cleanup_lock:
            now = self.clock.now()
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

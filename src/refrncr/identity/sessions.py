"""Identity browser-session service over a neutral persistence port."""

import hashlib
import secrets
import threading
from datetime import timedelta

from refrncr.clock import SYSTEM_CLOCK, Clock
from refrncr.ports import LoginSessionRecord, LoginSessionStore, UserRecord


class SessionService:
    def __init__(
        self,
        store: LoginSessionStore,
        lifetime: timedelta = timedelta(hours=12),
        *,
        clock: Clock = SYSTEM_CLOCK,
        retention: timedelta = timedelta(days=7),
        cleanup_batch_size: int = 100,
    ):
        self.store = store
        self.lifetime = lifetime
        self.clock = clock
        self.retention = retention
        self.cleanup_batch_size = cleanup_batch_size
        self._cleanup_cursor: str | None = None
        self._cleanup_lock = threading.Lock()

    def create(self, user: UserRecord) -> tuple[str, LoginSessionRecord]:
        self.cleanup()
        token = secrets.token_urlsafe(48)
        now = self.clock.now()
        login = self.store.create(
            user_id=user.id,
            token_hash=self._digest(token),
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            expires_at=now + self.lifetime,
        )
        return token, login

    def resolve(self, token: str) -> UserRecord | None:
        resolved = self.resolve_with_session(token)
        return resolved[0] if resolved else None

    def resolve_with_session(self, token: str) -> tuple[UserRecord, LoginSessionRecord] | None:
        return self.store.resolve(self._digest(token), self.clock.now())

    def revoke(self, token: str) -> None:
        self.store.revoke(self._digest(token), self.clock.now())
        self.cleanup()

    def cleanup(self) -> int:
        """Inspect and delete at most one primary-key page of retained sessions."""
        with self._cleanup_lock:
            deleted, self._cleanup_cursor = self.store.cleanup(
                self.clock.now() - self.retention,
                self._cleanup_cursor,
                self.cleanup_batch_size,
            )
            return deleted

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

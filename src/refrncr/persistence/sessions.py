"""SQLAlchemy browser-session adapter."""

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from refrncr.persistence.models import LoginSession, User
from refrncr.ports import LoginSessionRecord


def _record(login: LoginSession) -> LoginSessionRecord:
    return LoginSessionRecord(
        id=login.id,
        user_id=login.user_id,
        token_hash=login.token_hash,
        csrf_token=login.csrf_token,
        created_at=login.created_at,
        expires_at=login.expires_at,
        revoked_at=login.revoked_at,
    )


class SqlAlchemyLoginSessionStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def create(self, **values) -> LoginSessionRecord:
        login = LoginSession(**values)
        with self.sessions.begin() as session:
            session.add(login)
            session.flush()
            result = _record(login)
        return result

    def resolve(self, token_hash, now):
        with self.sessions() as session:
            login = session.scalar(
                select(LoginSession).where(LoginSession.token_hash == token_hash)
            )
            if login is None or login.revoked_at is not None or login.expires_at <= now:
                return None
            user = session.get(User, login.user_id)
            if user is None or not user.is_active or user.is_deleted:
                return None
            session.expunge(user)
            return user, _record(login)

    def revoke(self, token_hash, revoked_at) -> None:
        with self.sessions.begin() as session:
            session.execute(
                update(LoginSession)
                .where(LoginSession.token_hash == token_hash)
                .values(revoked_at=revoked_at)
            )

    def cleanup(self, cutoff, cursor, batch_size):
        with self.sessions.begin() as session:
            statement = select(LoginSession.id).order_by(LoginSession.id).limit(batch_size)
            if cursor is not None:
                statement = statement.where(LoginSession.id > cursor)
            ids = list(session.scalars(statement))
            if not ids:
                return 0, None
            next_cursor = ids[-1] if len(ids) == batch_size else None
            result = session.execute(
                delete(LoginSession).where(
                    LoginSession.id.in_(ids),
                    or_(LoginSession.expires_at <= cutoff, LoginSession.revoked_at <= cutoff),
                )
            )
            return result.rowcount or 0, next_cursor

"""Focused API-key operations participating in the caller's transaction."""

from sqlalchemy import or_, select, update

from refrncr.models import ApiKey


class SqlAlchemyApiKeyOperations:
    def __init__(self, session):
        self.session = session

    def get(self, key_id, *, fresh=False, for_update=False):
        statement = select(ApiKey).where(ApiKey.id == key_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement.execution_options(populate_existing=fresh))

    def get_owned(self, owner_id, key_id):
        return self.session.scalar(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.owner_id == owner_id)
        )

    def list_owned(self, owner_id):
        return tuple(
            self.session.scalars(
                select(ApiKey)
                .where(ApiKey.owner_id == owner_id)
                .order_by(ApiKey.created_at.desc(), ApiKey.id)
            )
        )

    def active_owned(self, owner_id, now):
        return tuple(
            self.session.scalars(
                select(ApiKey).where(
                    ApiKey.owner_id == owner_id,
                    ApiKey.revoked_at.is_(None),
                    ApiKey.expires_at > now,
                )
            )
        )

    def create(self, **values):
        key = ApiKey(**values)
        self.session.add(key)
        return key

    def update_last_used(self, key_id, *, used_at, cutoff):
        """Record recent use only when the persisted indication is stale."""
        result = self.session.execute(
            update(ApiKey)
            .where(
                ApiKey.id == key_id,
                or_(ApiKey.last_used_at.is_(None), ApiKey.last_used_at < cutoff),
            )
            .values(last_used_at=used_at)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

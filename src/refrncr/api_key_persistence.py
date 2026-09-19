"""Focused API-key operations participating in the caller's transaction."""

from sqlalchemy import select

from refrncr.models import ApiKey


class SqlAlchemyApiKeyOperations:
    def __init__(self, session):
        self.session = session

    def get(self, key_id, *, fresh=False):
        return self.session.scalar(
            select(ApiKey).where(ApiKey.id == key_id).execution_options(populate_existing=fresh)
        )

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

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError
from support import MutableClock

from refrncr.clock import SYSTEM_CLOCK
from refrncr.db.base import Base
from refrncr.db.types import UTCDateTime
from refrncr.models import AuditEvent, User


def test_every_orm_datetime_column_uses_utc_datetime_type():
    columns = [
        column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, UTCDateTime)
    ]
    assert len(columns) == 28


def test_database_roundtrip_normalizes_aware_value_and_rejects_naive(database_record_services):
    _, sessions, _, admin = database_record_services
    instant = datetime(2026, 10, 19, 18, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))
    with sessions.begin() as session:
        user = session.get(User, admin.id)
        user.created_at = instant
    with sessions() as session:
        saved = session.get(User, admin.id).created_at
        assert saved.tzinfo is UTC
        assert saved == instant.astimezone(UTC)

    with pytest.raises(StatementError, match="timezone-aware"):
        with sessions.begin() as session:
            user = session.get(User, admin.id)
            user.created_at = datetime(2026, 10, 19, 18, 30)  # noqa: DTZ001 - rejected value


def test_injected_clock_stamps_service_orm_and_audit(database_record_services):
    _, sessions, services, admin = database_record_services
    clock = MutableClock(SYSTEM_CLOCK.now())
    services.users.uow.clock = clock
    created_at = datetime(2026, 10, 19, 12, tzinfo=UTC)
    clock.set(created_at)
    user = services.users.create_user(admin.id, "clock@example.com", "Clock", "valid password")
    with sessions() as session:
        stored = session.get(User, user.id)
        created_event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.object_id == user.id,
                AuditEvent.object_type == "user",
                AuditEvent.action == "created",
            )
        )
        assert stored.created_at == created_at
        assert stored.updated_at == created_at
        assert created_event.timestamp == created_at

    updated_at = datetime(2026, 10, 19, 13, tzinfo=UTC)
    clock.set(updated_at)
    services.users.change_email(admin.id, user.id, "clock-updated@example.com")
    with sessions() as session:
        stored = session.get(User, user.id)
        updated_event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.object_id == user.id,
                AuditEvent.object_type == "user",
                AuditEvent.action == "email_changed",
            )
        )
        assert stored.updated_at == updated_at
        assert updated_event.timestamp == updated_at


def test_clock_today_uses_application_timezone_at_day_boundary():
    local_time = datetime(2027, 1, 1, 0, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))
    clock = MutableClock(local_time)
    assert clock.now() == datetime(2026, 12, 31, 23, 30, tzinfo=UTC)
    assert clock.today() == date(2026, 12, 31)
    assert SYSTEM_CLOCK.now().utcoffset().total_seconds() == 0

"""Application clock shared by services and persistence."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol

from refrncr.version_contracts import APPLICATION_TIMEZONE


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current timezone-aware instant in UTC."""

    def today(self) -> date:
        """Return today's date in the application timezone."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        return self.now().astimezone(APPLICATION_TIMEZONE).date()


SYSTEM_CLOCK = SystemClock()

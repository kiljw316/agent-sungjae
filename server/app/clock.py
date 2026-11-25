"""Injectable clock so timers can be tested deterministically."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock(Clock):
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def ensure_utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo; values are always stored as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

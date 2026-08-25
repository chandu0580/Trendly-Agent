"""The single source of "now" for every time-sensitive rule.

Two things this exists to prevent:

**Scattered clocks.** `datetime.now()` inside business logic makes a rule
untestable at its boundary and makes the answer depend on when the suite runs.
Every date calculation reads the clock passed to it.

**Silent timezone drift.** The policy's windows are customer-facing dates in IST,
but the dataset records timestamps in UTC. Truncating `2026-07-26T20:00:00Z` to
its UTC date gives 26 July when the customer's calendar says the 27th — a
one-day error at exactly the boundary where it matters. Conversion is done here,
once.

There is deliberately no holiday calendar. The policy says "business day", the
supplied document provides no holiday list, and inventing one would be worse
than the documented approximation: business days are treated as calendar days.
That assumption is recorded in docs/architecture.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

# Trendly's customer-facing timezone. Every policy window is a date in IST.
IST = timezone(timedelta(hours=5, minutes=30))


class Clock(Protocol):
    def now(self) -> datetime:
        """The current instant, timezone-aware."""

    def today(self) -> date:
        """Today's date on the customer's calendar (IST)."""


@dataclass(frozen=True)
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def today(self) -> date:
        return self.now().astimezone(IST).date()


@dataclass(frozen=True)
class FixedClock:
    """A clock pinned to one instant, for tests and the demo reference date."""

    instant: datetime

    @classmethod
    def at(cls, value: str) -> "FixedClock":
        """From an ISO timestamp, or a bare date (taken as 00:00 IST)."""
        text = value.strip()
        if len(text) == 10:
            return cls(datetime.fromisoformat(text).replace(tzinfo=IST))
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return cls(parsed if parsed.tzinfo else parsed.replace(tzinfo=IST))

    def now(self) -> datetime:
        return self.instant

    def today(self) -> date:
        return self.instant.astimezone(IST).date()


def to_ist_datetime(timestamp: str | None) -> datetime | None:
    """Parse a dataset timestamp into an IST-aware datetime.

    A bare `YYYY-MM-DD` is read as midnight IST — the dataset uses bare dates for
    customer-facing expectations like `expected_delivery`, which are already IST.
    """
    if not timestamp:
        return None
    text = timestamp.strip()
    if len(text) == 10:
        return datetime.fromisoformat(text).replace(tzinfo=IST)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(IST)


def to_ist_date(timestamp: str | None) -> date | None:
    """The customer-calendar date of a dataset timestamp."""
    moment = to_ist_datetime(timestamp)
    return moment.date() if moment else None


def as_ist_instant(value: date | datetime | None) -> datetime | None:
    """Normalise a request clock to an IST-aware instant.

    A bare `date` has no time of day, and the choice of which moment to assume
    decides borderline cases. It becomes *start* of day IST: that is the earliest
    the request could have been made, so an hour-granular window stays open as
    long as the date allows. Taking end-of-day would expire windows sooner —
    against the customer — on exactly the boundary where it matters.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(IST) if value.tzinfo else value.replace(tzinfo=IST)
    return datetime.combine(value, datetime.min.time()).replace(tzinfo=IST)


_clock: Clock = SystemClock()


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> None:
    """Install a clock. Used by configuration and by tests; never by a tool."""
    global _clock
    _clock = clock


def reset_clock() -> None:
    set_clock(SystemClock())

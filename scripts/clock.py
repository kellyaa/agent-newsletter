"""Centralized time source for the newsletter pipeline.

All pipeline modules that need "the current time" should import from here
rather than calling ``datetime.now()`` directly. This provides a single
seam for:

1. **Deterministic testing** — tests can freeze or advance the clock without
   monkeypatching datetime or using fragile mocks.
2. **Backfill/replay** — the backfill script can pin the clock to a synthetic
   date so that recency windows and "today" computations behave as if the
   pipeline ran on the target date.

Usage in pipeline modules::

    from clock import now, today

    current_time = now()          # → datetime with tzinfo=UTC
    date_str = today()            # → "YYYY-MM-DD" string

Usage in tests::

    from clock import freeze

    with freeze(datetime(2026, 1, 15, tzinfo=timezone.utc)):
        assert today() == "2026-01-15"

Usage in backfill::

    import clock
    clock.set_frozen(target_datetime)
    # ... all downstream imports of now()/today() return the frozen time
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from typing import Generator

# When set, now() returns this fixed value instead of the real wall clock.
_frozen_time: datetime | None = None


def now() -> datetime:
    """Return the current UTC datetime, or the frozen time if set."""
    if _frozen_time is not None:
        return _frozen_time
    return datetime.now(timezone.utc)


def today() -> str:
    """Return today's date as an ISO string (YYYY-MM-DD)."""
    return now().date().isoformat()


def set_frozen(dt: datetime | None) -> None:
    """Set (or clear) the frozen clock.

    Pass a timezone-aware datetime to freeze, or None to restore real time.
    Prefer the ``freeze`` context manager for scoped usage.
    """
    global _frozen_time
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    _frozen_time = dt


@contextlib.contextmanager
def freeze(dt: datetime) -> Generator[None, None, None]:
    """Context manager to freeze the clock at a specific instant.

    ::

        with freeze(datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)):
            assert today() == "2026-06-15"
    """
    previous = _frozen_time
    set_frozen(dt)
    try:
        yield
    finally:
        set_frozen(previous)

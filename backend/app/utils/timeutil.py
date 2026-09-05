"""Timezone normalisation for values that make a round trip through the database.

The whole platform works in UTC, and every value is *written* as an aware UTC
datetime. SQLite, however, has no timezone type: it stores the wall-clock part
and hands back a naive datetime on read. PostgreSQL with `TIMESTAMP WITHOUT
TIME ZONE` behaves the same way.

The result is a comparison that works in SQL (where both sides are strings or
native timestamps) but raises `TypeError: can't compare offset-naive and
offset-aware datetimes` the moment the same check is done in Python. That
failure is silent until a code path with a Python-side comparison is exercised,
which is exactly how a dashboard endpoint can 500 while every query behind it
succeeds.

`as_utc` is the single place that fixes it: naive values are *assumed* UTC,
because that is the only thing this application ever writes.
"""

from datetime import datetime, timezone


def as_utc(value: datetime | None) -> datetime | None:
    """Return `value` as an aware UTC datetime, or None."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

"""UTC time helpers for DB storage and API serialization."""

import datetime

UTC = datetime.timezone.utc


def utc_now_naive() -> datetime.datetime:
    """Return current UTC time without tzinfo for existing DateTime columns."""
    return datetime.datetime.now(UTC).replace(tzinfo=None)


def as_utc_aware(value: datetime.datetime | None) -> datetime.datetime | None:
    """Treat naive datetimes from the DB as UTC and return an aware value."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utc_isoformat(value: datetime.datetime | None) -> str | None:
    """Serialize DB datetimes with an explicit UTC offset."""
    aware = as_utc_aware(value)
    return aware.isoformat() if aware else None

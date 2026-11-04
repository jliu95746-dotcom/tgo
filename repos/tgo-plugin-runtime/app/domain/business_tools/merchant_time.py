"""Normalize merchant timestamps; never guess during DST transitions."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def normalize_merchant_time(value: str | datetime, timezone_name: str) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)
    if timezone_name == "UTC":
        return parsed.replace(tzinfo=UTC)
    zone = ZoneInfo(timezone_name)
    candidates = {
        candidate.astimezone(UTC)
        for fold in (0, 1)
        if (candidate := parsed.replace(tzinfo=zone, fold=fold))
        .astimezone(UTC)
        .astimezone(zone)
        .replace(tzinfo=None)
        == parsed
    }
    if len(candidates) != 1:
        raise ValueError("merchant time is ambiguous or does not exist")
    return candidates.pop()

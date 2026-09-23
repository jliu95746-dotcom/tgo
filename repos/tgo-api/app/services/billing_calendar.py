"""Calendar subscriptions and exact proration in integer fen (1/100 yuan)."""

import calendar
from datetime import datetime, timedelta
from fractions import Fraction
from math import ceil


def add_months(
    value: datetime, months: int, *, anchor_day: int | None = None
) -> datetime:
    ordinal = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(ordinal, 12)
    month = month_index + 1
    day = min(anchor_day or value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def microseconds(value: timedelta) -> int:
    return value.days * 86400000000 + value.seconds * 1000000 + value.microseconds


def remaining_fraction(start: datetime, end: datetime, now: datetime) -> Fraction:
    duration = microseconds(end - start)
    if duration <= 0:
        raise ValueError("Billing period must have positive duration")
    remaining = max(0, microseconds(end - max(start, now)))
    return Fraction(remaining, duration)


def prorated_charge(
    periods: list[tuple[int, datetime, datetime]], now: datetime
) -> int:
    amount = sum(
        (price * remaining_fraction(start, end, now) for price, start, end in periods),
        Fraction(0),
    )
    return max(0, ceil(amount))

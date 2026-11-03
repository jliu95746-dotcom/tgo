"""Clock rules for durable workflow schedules, independent of a worker."""

from datetime import datetime, timezone
from typing import cast

import pytest

from app.services.schedule_rules import ScheduleRuleError, parse_schedule


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


@pytest.mark.parametrize(
    ("expression", "zone", "after", "expected"),
    [
        (
            "* * * * *",
            "UTC",
            "2026-10-24T12:00:00+00:00",
            "2026-10-24T12:01:00+00:00",
        ),
        (
            "* * * * *",
            "UTC",
            "2026-10-24T12:00:59+00:00",
            "2026-10-24T12:01:00+00:00",
        ),
        (
            "0 9 * * *",
            "Asia/Shanghai",
            "2026-10-24T00:59:59+00:00",
            "2026-10-24T01:00:00+00:00",
        ),
        (
            "0 9 * * *",
            "Asia/Shanghai",
            "2026-10-24T01:00:00+00:00",
            "2026-10-25T01:00:00+00:00",
        ),
        (
            "5/15 * * * *",
            "UTC",
            "2026-10-24T12:05:00+00:00",
            "2026-10-24T12:20:00+00:00",
        ),
        (
            "0 9 * * 1-5",
            "UTC",
            "2026-10-23T09:00:00+00:00",
            "2026-10-26T09:00:00+00:00",
        ),
        (
            "0 0 * * 7",
            "UTC",
            "2026-10-24T12:00:00+00:00",
            "2026-10-25T00:00:00+00:00",
        ),
        (
            "0 0 29 2 *",
            "UTC",
            "2026-10-24T12:00:00+00:00",
            "2028-02-29T00:00:00+00:00",
        ),
        (
            "0 0 31 * *",
            "UTC",
            "2026-04-01T00:00:00+00:00",
            "2026-05-31T00:00:00+00:00",
        ),
        # Limited day-of-month and weekday are alternatives, within the month.
        (
            "0 0 31 2 1",
            "UTC",
            "2026-02-01T00:00:00+00:00",
            "2026-02-02T00:00:00+00:00",
        ),
        (
            "0 0 1 11 1",
            "UTC",
            "2026-11-01T00:00:00+00:00",
            "2026-11-02T00:00:00+00:00",
        ),
        # A nonexistent local minute is skipped, not moved to another time.
        (
            "30 2 * * *",
            "America/New_York",
            "2026-03-08T00:00:00+00:00",
            "2026-03-09T06:30:00+00:00",
        ),
        # An ambiguous minute runs only at its first UTC instant.
        (
            "30 1 * * *",
            "America/New_York",
            "2026-11-01T00:00:00+00:00",
            "2026-11-01T05:30:00+00:00",
        ),
        (
            "30 1 * * *",
            "America/New_York",
            "2026-11-01T05:30:00+00:00",
            "2026-11-02T06:30:00+00:00",
        ),
        (
            "30 1 * * *",
            "America/New_York",
            "2026-11-01T06:00:00+00:00",
            "2026-11-02T06:30:00+00:00",
        ),
    ],
)
def test_next_occurrence_is_strict_and_uses_the_selected_zone(
    expression: str,
    zone: str,
    after: str,
    expected: str,
) -> None:
    result = parse_schedule(expression, zone).next_occurrences(instant(after))
    assert result == (instant(expected),)
    assert result[0].tzinfo is timezone.utc


def test_lists_ranges_and_steps_are_normalized_without_duplicate_minutes() -> (
    None
):
    rule = parse_schedule(" 0,0,10-20/5   9-10 * * 0,7 ", "UTC")
    assert rule.expression == "0,0,10-20/5 9-10 * * 0,7"
    assert rule.minutes == frozenset({0, 10, 15, 20})
    assert rule.weekdays == frozenset({0})
    assert rule.next_occurrences(
        instant("2026-10-24T23:59:00+00:00"), count=5
    ) == (
        instant("2026-10-25T09:00:00+00:00"),
        instant("2026-10-25T09:10:00+00:00"),
        instant("2026-10-25T09:15:00+00:00"),
        instant("2026-10-25T09:20:00+00:00"),
        instant("2026-10-25T10:00:00+00:00"),
    )


def test_sparse_valid_schedule_can_preview_multiple_leap_years() -> None:
    result = parse_schedule("0 0 29 2 *").next_occurrences(
        instant("2026-01-01T00:00:00+00:00"),
        count=5,
    )
    assert [value.year for value in result] == [2028, 2032, 2036, 2040, 2044]


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "* * * *",
        "* * * * * *",
        "@daily",
        "60 * * * *",
        "-1 * * * *",
        "0 24 * * *",
        "0 0 0 * *",
        "0 0 * 13 *",
        "0 0 * * 8",
        "0 0 * * MON",
        "*/0 * * * *",
        "*/61 * * * *",
        "1-0 * * * *",
        "1,,2 * * * *",
        "1, * * * *",
        "1- * * * *",
        "*/2/3 * * * *",
        "0 0 L * *",
        "0\n0 * * *",
        "０ * * * *",
        "0 " + "*" * 510,
        "0 0 * * 1#2",
        "0 0 31 2 0-7",
    ],
)
def test_invalid_rule_is_rejected_without_echoing_untrusted_text(
    expression: str,
) -> None:
    with pytest.raises(ScheduleRuleError) as error:
        parse_schedule(expression)
    assert len(str(error.value)) < 120


@pytest.mark.parametrize(
    "zone", ["", "Unknown/PrivateTenant", "../UTC", "/etc/passwd", "UTC\n"]
)
def test_invalid_zone_is_rejected_without_echoing_its_value(zone: str) -> None:
    with pytest.raises(ScheduleRuleError) as error:
        parse_schedule("0 9 * * *", zone)
    assert "PrivateTenant" not in str(error.value)
    assert "passwd" not in str(error.value)


def test_impossible_calendar_is_bounded_and_rejected() -> None:
    with pytest.raises(ScheduleRuleError, match="日期"):
        parse_schedule("0 0 30 2 *").next_occurrences(
            instant("2026-01-01T00:00:00+00:00"),
        )


@pytest.mark.parametrize("count", [0, -1, 11, True, 1.5])
def test_preview_count_is_strict_and_bounded(count: object) -> None:
    with pytest.raises(ScheduleRuleError):
        parse_schedule("* * * * *").next_occurrences(
            instant("2026-01-01T00:00:00+00:00"),
            count=cast(int, count),
        )


def test_naive_clock_cannot_silently_use_the_server_timezone() -> None:
    with pytest.raises(ScheduleRuleError, match="时区"):
        parse_schedule("* * * * *").next_occurrences(datetime(2026, 1, 1))


def test_datetime_overflow_is_a_controlled_error() -> None:
    with pytest.raises(ScheduleRuleError):
        parse_schedule("0 0 1 1 *").next_occurrences(
            instant("9999-12-31T23:59:59+00:00"),
        )

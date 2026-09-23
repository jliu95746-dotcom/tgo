"""Calendar anchors and monetary proration use exact integer arithmetic."""

from datetime import datetime, timezone

from app.services.billing_calendar import add_months, prorated_charge


def test_month_end_anchor_returns_to_31_after_february():
    start = datetime(2026, 1, 31, 12, 30, tzinfo=timezone.utc)
    february = add_months(start, 1, anchor_day=31)
    assert february.day == 28
    assert add_months(february, 1, anchor_day=31) == datetime(
        2026, 3, 31, 12, 30, tzinfo=timezone.utc
    )


def test_leap_year_annual_renewal_preserves_original_anchor():
    start = datetime(2024, 2, 29, tzinfo=timezone.utc)
    assert add_months(start, 12, anchor_day=29).day == 28
    assert add_months(start, 48, anchor_day=29).day == 29


def test_proration_rounds_only_after_summing_all_periods():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 4, tzinfo=timezone.utc)
    now = datetime(2026, 1, 3, tzinfo=timezone.utc)
    assert prorated_charge([(1, start, end), (1, start, end)], now) == 1


def test_future_period_is_charged_in_full_and_expired_period_is_zero():
    now = datetime(2026, 2, 1, tzinfo=timezone.utc)
    assert (
        prorated_charge(
            [
                (1000, datetime(2026, 1, 1, tzinfo=timezone.utc), now),
                (2000, now, datetime(2026, 3, 1, tzinfo=timezone.utc)),
            ],
            now,
        )
        == 2000
    )

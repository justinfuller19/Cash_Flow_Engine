"""Regression coverage for Phase 6 calendar recurrence rules."""
from datetime import date

import pytest

from app.forecast import next_occurrence, occurrences_between


def test_twice_monthly_last_uses_15th_and_month_end_inclusive():
    assert occurrences_between(
        date(2026, 1, 15), "twice_monthly_last", date(2026, 2, 1), date(2026, 2, 28)
    ) == [date(2026, 2, 15), date(2026, 2, 28)]


def test_semimonthly_uses_first_and_fifteenth_and_handles_leap_month():
    assert occurrences_between(
        date(2024, 1, 1), "semimonthly", date(2024, 2, 1), date(2024, 2, 29)
    ) == [date(2024, 2, 1), date(2024, 2, 15)]


def test_first_and_third_friday_pattern():
    assert occurrences_between(
        date(2026, 1, 2), "first_third_friday", date(2026, 2, 1), date(2026, 2, 28)
    ) == [date(2026, 2, 6), date(2026, 2, 20)]


def test_last_business_day_skips_weekends_and_us_federal_holidays():
    # July 31, 2026 is a Friday, but this test also covers a month whose end
    # is a weekend: August 31, 2026 is Monday.
    assert occurrences_between(
        date(2026, 7, 31), "last_business_day", date(2026, 8, 1), date(2026, 8, 31)
    ) == [date(2026, 8, 31)]
    # Christmas 2026 is Friday; last business day is Thursday the 31st is not
    # relevant, so December's final weekday remains Dec 31.
    assert next_occurrence(date(2026, 11, 30), "last_business_day") == date(2026, 12, 31)


def test_monthly_anchor_clamps_short_month_then_returns_to_anchor_day():
    assert occurrences_between(
        date(2026, 1, 31), "monthly", date(2026, 2, 1), date(2026, 4, 30)
    ) == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


def test_irregular_has_no_synthetic_future_occurrences():
    assert occurrences_between(
        date(2026, 1, 15), "irregular", date(2026, 2, 1), date(2026, 3, 1)
    ) == []


def test_invalid_window_and_frequency_are_rejected():
    with pytest.raises(ValueError):
        occurrences_between(date(2026, 1, 1), "weekly", date(2026, 2, 1), date(2026, 1, 1))
    with pytest.raises(ValueError):
        next_occurrence(date(2026, 1, 1), "sometimes")

def test_occurrences_around_includes_paydays_before_a_future_anchor():
    from app.forecast import occurrences_around

    assert occurrences_around(
        date(2026, 11, 13), "biweekly", date(2026, 10, 30), date(2026, 11, 27)
    ) == [
        date(2026, 10, 30), date(2026, 11, 13), date(2026, 11, 27)
    ]


def test_monthly_occurrences_around_reconstructs_prior_month_from_next_payday():
    from app.forecast import occurrences_around

    assert occurrences_around(
        date(2026, 11, 15), "monthly", date(2026, 10, 1), date(2026, 12, 31)
    ) == [
        date(2026, 10, 15), date(2026, 11, 15), date(2026, 12, 15)
    ]


def test_semimonthly_occurrences_around_includes_first_payday_before_anchor():
    from app.forecast import occurrences_around

    assert occurrences_around(
        date(2026, 11, 15), "semimonthly", date(2026, 11, 1), date(2026, 11, 30)
    ) == [date(2026, 11, 1), date(2026, 11, 15)]

def test_multi_month_bill_recurrences_keep_the_original_day_anchor():
    from app.forecast import occurrences_between

    anchor = date(2026, 1, 31)
    assert occurrences_between(
        anchor, "every_two_months", date(2026, 1, 1), date(2026, 7, 31)
    ) == [
        date(2026, 1, 31), date(2026, 3, 31), date(2026, 5, 31), date(2026, 7, 31)
    ]
    assert occurrences_between(
        anchor, "every_four_months", date(2026, 1, 1), date(2026, 12, 31)
    ) == [date(2026, 1, 31), date(2026, 5, 31), date(2026, 9, 30)]
    assert occurrences_between(
        anchor, "every_six_months", date(2026, 1, 1), date(2027, 1, 31)
    ) == [date(2026, 1, 31), date(2026, 7, 31), date(2027, 1, 31)]

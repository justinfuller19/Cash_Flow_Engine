"""Calendar recurrence helpers for payday and bill forecasting.

Monthly schedules are anchored to calendar rules, not repeated day offsets.
Last-business-day schedules exclude weekends and commonly observed US bank
holidays, including Good Friday. Other named schedules retain their calendar
dates unless their name explicitly says business day.
"""
from calendar import monthrange
from datetime import date, timedelta


def _shift_month(cursor: date, months: int, anchor_day: int) -> date:
    month_index = cursor.year * 12 + (cursor.month - 1) + months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    day = min(anchor_day, monthrange(year, month)[1])
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month, monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed_fixed_holiday(year: int, month: int, day_of_month: int) -> date:
    actual = date(year, month, day_of_month)
    if actual.weekday() == 5:
        return actual - timedelta(days=1)
    if actual.weekday() == 6:
        return actual + timedelta(days=1)
    return actual


def _easter_sunday(year: int) -> date:
    # Gregorian computus; used only to calculate Good Friday.
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day_of_month = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day_of_month)


def _us_federal_holidays(year: int) -> set[date]:
    holidays = {
        _observed_fixed_holiday(year, 1, 1),
        _nth_weekday(year, 1, 0, 3),   # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),   # Washington's Birthday
        _last_weekday(year, 5, 0),     # Memorial Day
        
        _observed_fixed_holiday(year, 7, 4),
        _nth_weekday(year, 9, 0, 1),   # Labor Day
        _nth_weekday(year, 10, 0, 2),  # Columbus Day
        _observed_fixed_holiday(year, 11, 11),
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed_fixed_holiday(year, 12, 25),
        _easter_sunday(year) - timedelta(days=2),  # Good Friday (bank convention)
    }
    if year >= 2021:
        holidays.add(_observed_fixed_holiday(year, 6, 19))  # Juneteenth
    return holidays


def _is_business_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    holidays = _us_federal_holidays(day.year)
    # Also recognize adjacent-year observed New Year's Day.
    holidays.update(_us_federal_holidays(day.year - 1))
    holidays.update(_us_federal_holidays(day.year + 1))
    return day not in holidays


def _last_business_day(year: int, month: int) -> date:
    cursor = date(year, month, monthrange(year, month)[1])
    while not _is_business_day(cursor):
        cursor -= timedelta(days=1)
    return cursor


def _monthly_dates(year: int, month: int, frequency: str, anchor_day: int) -> list[date]:
    last = monthrange(year, month)[1]
    if frequency == "semimonthly":
        return [date(year, month, 1), date(year, month, 15)]
    if frequency == "twice_monthly_last":
        return [date(year, month, 15), date(year, month, last)]
    if frequency == "first_third_friday":
        return [_nth_weekday(year, month, 4, 1), _nth_weekday(year, month, 4, 3)]
    if frequency == "last_business_day":
        return [_last_business_day(year, month)]
    if frequency == "monthly":
        return [date(year, month, min(anchor_day, last))]
    raise ValueError(f"Unsupported monthly recurrence frequency: {frequency}")


def next_occurrence(cursor: date, frequency: str, anchor_day: int | None = None) -> date | None:
    """Return the next scheduled occurrence strictly after cursor."""
    if frequency == "weekly":
        return cursor + timedelta(days=7)
    if frequency == "biweekly":
        return cursor + timedelta(days=14)
    if frequency in {"semimonthly", "twice_monthly_last", "first_third_friday", "last_business_day"}:
        dates = _monthly_dates(cursor.year, cursor.month, frequency, anchor_day or cursor.day)
        for candidate in dates:
            if candidate > cursor:
                return candidate
        next_month = _shift_month(cursor, 1, 1)
        return _monthly_dates(next_month.year, next_month.month, frequency, anchor_day or cursor.day)[0]
    if frequency == "monthly":
        return _shift_month(cursor, 1, anchor_day or cursor.day)
    if frequency == "quarterly":
        return _shift_month(cursor, 3, anchor_day or cursor.day)
    if frequency == "yearly":
        return _shift_month(cursor, 12, anchor_day or cursor.day)
    if frequency == "irregular":
        return None
    raise ValueError(f"Unsupported recurrence frequency: {frequency}")


def occurrences_between(anchor: date, frequency: str, start: date, end: date) -> list[date]:
    """Return all predictable occurrences in an inclusive date window.

    Existing weekly/biweekly/monthly/quarterly/yearly anchors remain stable.
    Irregular items occur only on their explicit anchor date. Monthly rule
    schedules use their named calendar pattern and handle short months/leap years.
    """
    if end < start:
        raise ValueError("end must be on or after start")
    if frequency == "irregular":
        return [anchor] if start <= anchor <= end else []
    if frequency not in {
        "weekly", "biweekly", "semimonthly", "twice_monthly_last",
        "first_third_friday", "last_business_day", "monthly", "quarterly", "yearly",
    }:
        raise ValueError(f"Unsupported recurrence frequency: {frequency}")
    anchor_day = anchor.day
    cursor = anchor
    results: list[date] = []
    iterations = 0
    while cursor < start:
        following = next_occurrence(cursor, frequency, anchor_day)
        if following is None:
            return []
        if following <= cursor:
            raise ValueError("recurrence did not advance")
        cursor = following
        iterations += 1
        if iterations > 5000:
            raise ValueError("date window is too large to forecast")
    while cursor <= end:
        results.append(cursor)
        following = next_occurrence(cursor, frequency, anchor_day)
        if following is None:
            break
        if following <= cursor:
            raise ValueError("recurrence did not advance")
        cursor = following
        iterations += 1
        if iterations > 5000:
            raise ValueError("date window is too large to forecast")
    return results

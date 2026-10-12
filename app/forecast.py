"""Calendar recurrence helpers for payday and bill forecasting."""
from calendar import monthrange
from datetime import date, timedelta


def _shift_month(cursor: date, months: int, anchor_day: int) -> date:
    month_index = cursor.year * 12 + (cursor.month - 1) + months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    day = min(anchor_day, monthrange(year, month)[1])
    return date(year, month, day)


def next_occurrence(cursor: date, frequency: str, anchor_day: int | None = None) -> date | None:
    """Return the next scheduled occurrence, preserving the original month day."""
    if frequency == "weekly":
        return cursor + timedelta(days=7)
    if frequency == "biweekly":
        return cursor + timedelta(days=14)
    if frequency == "semimonthly":
        if cursor.day < 15:
            return cursor.replace(day=15)
        next_month = _shift_month(cursor, 1, 1)
        return next_month.replace(day=1)
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

    Irregular income/bills are not extrapolated beyond their explicitly saved date.
    Monthly/quarterly/yearly schedules preserve the original day when shorter months
    require clamping (for example, a 31st-of-month bill returns to the 31st later).
    """
    if end < start:
        raise ValueError("end must be on or after start")
    if frequency == "irregular":
        return [anchor] if start <= anchor <= end else []
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

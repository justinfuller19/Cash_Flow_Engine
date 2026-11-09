"""Pay-period calendar primitives.

Paychecks are grouped relative to the first paycheck in each group. The
period begins on the group's final payday; earlier days in the group remain
assigned to the preceding period.
"""
from datetime import date, timedelta


def group_paychecks(paycheck_dates: list[date], window_days: int = 2) -> list[dict]:
    """Group paydays against a fixed anchor, then assign period boundaries.

    A payday joins a group only when it is within the configured number of
    days of that group first payday. Comparing against the anchor prevents
    transitive chaining (for example, Monday/Wednesday/Friday).
    """
    if isinstance(window_days, bool) or not isinstance(window_days, int) or not 0 <= window_days <= 14:
        raise ValueError("window_days must be an integer between 0 and 14")
    dates = sorted(set(paycheck_dates))
    if any(not isinstance(value, date) for value in dates):
        raise TypeError("paycheck_dates must contain date values")
    grouped: list[list[date]] = []
    for payday in dates:
        if not grouped or (payday - grouped[-1][0]).days > window_days:
            grouped.append([payday])
        else:
            grouped[-1].append(payday)
    result = []
    for index, paydays in enumerate(grouped):
        period_start = paydays[-1]
        period_end = grouped[index + 1][-1] - timedelta(days=1) if index + 1 < len(grouped) else None
        result.append({
            "first_paycheck_date": paydays[0],
            "last_paycheck_date": paydays[-1],
            "paycheck_dates": tuple(paydays),
            "period_start": period_start,
            "period_end": period_end,
        })
    return result


def build_pay_period_calendar(
    paycheck_dates: list[date],
    today: date,
    horizon_end: date,
    window_days: int = 2,
) -> list[dict]:
    """Return the current period and future periods starting within the horizon.

    Callers should supply enough historical paycheck dates to include the
    paycheck group that established the current period, plus future paydays
    through at least the horizon end. A period is bounded by the next group's
    final payday, not its first payday.
    """
    if horizon_end < today:
        raise ValueError("horizon_end must be on or after today")
    groups = group_paychecks(paycheck_dates, window_days)
    current_index = next(
        (
            index for index, group in enumerate(groups)
            if group["period_start"] <= today
            and (group["period_end"] is None or today <= group["period_end"])
        ),
        None,
    )
    if current_index is None:
        raise ValueError("paycheck_dates must include the group containing today")
    return [
        {
            **group,
            "is_current_period": index == current_index,
            "unreceived_paycheck_dates": tuple(
                payday for payday in group["paycheck_dates"] if payday > today
            ),
        }
        for index, group in enumerate(groups[current_index:], start=current_index)
        if index == current_index or group["period_start"] <= horizon_end
    ]


def period_for_due_date(due_date: date, periods: list[dict]) -> dict | None:
    """Return the period owning a bill due date; period start is inclusive."""
    for period in periods:
        if period["period_start"] <= due_date and (
            period["period_end"] is None or due_date <= period["period_end"]
        ):
            return period
    return None

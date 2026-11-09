"""Pure pay-period cash projection and backward carry-over calculations."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

_OUTFLOW_FIELDS = ("bills_cents", "savings_cents", "debt_payments_cents", "other_expenses_cents")


def calculate_pay_periods(periods: Sequence[Mapping[str, int | str]], end_of_period_target_cents: int) -> dict:
    """Project ordered periods; first opening cash is today's confirmed snapshot.

    Later periods open with the prior projected ending balance. Income and
    outflow amounts are integer cents. The target is a soft goal, never an
    outflow or protected floor.
    """
    if isinstance(end_of_period_target_cents, bool) or not isinstance(end_of_period_target_cents, int) or end_of_period_target_cents < 0:
        raise ValueError("end_of_period_target_cents must be a non-negative integer")
    if not periods:
        return {"periods": [], "end_of_period_target_cents": end_of_period_target_cents,
                "reserved_cash_cents": 0, "max_spendable_cents": 0,
                "spendable_while_keeping_target_cents": 0, "target_maintainable": True, "warnings": []}

    projected = []
    for index, source in enumerate(periods):
        period = dict(source)
        for field in ("income_cents", *_OUTFLOW_FIELDS):
            value = period.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if index == 0:
            opening = period.get("starting_cash_cents")
            if isinstance(opening, bool) or not isinstance(opening, int) or opening < 0:
                raise ValueError("first period requires non-negative starting_cash_cents")
        else:
            opening = projected[index - 1]["projected_ending_cash_cents"]
        outflow = sum(period.get(field, 0) for field in _OUTFLOW_FIELDS)
        ending = opening + period.get("income_cents", 0) - outflow
        projected.append({**period, "starting_cash_cents": opening, "outflow_cents": outflow,
                          "projected_ending_cash_cents": ending})

    required = end_of_period_target_cents
    for index in range(len(projected) - 1, -1, -1):
        if index < len(projected) - 1:
            following = projected[index + 1]
            required = max(end_of_period_target_cents,
                           following["outflow_cents"] - following["income_cents"] + required)
        item = projected[index]
        item["required_ending_cash_cents"] = required
        item["carry_out_above_target_cents"] = required - end_of_period_target_cents
        item["target_shortfall_cents"] = max(0, required - item["projected_ending_cash_cents"])
        item["below_target_cents"] = max(0, end_of_period_target_cents - item["projected_ending_cash_cents"])
        item["negative_cash_shortfall_cents"] = max(0, -item["projected_ending_cash_cents"])

    max_spendable = max(0, min(p["projected_ending_cash_cents"] for p in projected))
    reserved = sum(p.get("bills_cents", 0) + p.get("savings_cents", 0) + p.get("debt_payments_cents", 0) for p in projected)
    warnings = []
    for item in projected:
        if item["projected_ending_cash_cents"] < 0:
            warnings.append({"type": "negative_cash", "start_date": item.get("start_date"),
                             "shortfall_cents": item["negative_cash_shortfall_cents"]})
        if item["target_shortfall_cents"] > 0:
            warnings.append({"type": "target_shortfall", "start_date": item.get("start_date"),
                             "shortfall_cents": item["target_shortfall_cents"]})
    return {
        "periods": projected, "end_of_period_target_cents": end_of_period_target_cents,
        "reserved_cash_cents": reserved, "max_spendable_cents": max_spendable,
        "spendable_while_keeping_target_cents": max(0, max_spendable - end_of_period_target_cents),
        "target_maintainable": all(p["target_shortfall_cents"] == 0 for p in projected),
        "warnings": warnings,
    }


def transaction_reservation_impact(transaction_amount_cents: int, available_cash_cents: int,
                                   reserved_bills: Sequence[Mapping[str, int | str]]) -> dict:
    """List new bill underfunding caused by a proposed expense; never mutates data."""
    for field, value in (("transaction_amount_cents", transaction_amount_cents),
                         ("available_cash_cents", available_cash_cents)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a non-negative integer")
    validated = []
    for bill in reserved_bills:
        amount, paid = bill.get("amount_cents", 0), bill.get("paid_cents", 0)
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (amount, paid)):
            raise ValueError("bill amounts must be non-negative integer cents")
        validated.append((bill, max(0, amount - paid)))

    def funded_by_bill(cash: int) -> list[int]:
        remaining = cash
        allocations = []
        for _, unpaid in validated:
            funded = min(unpaid, remaining)
            allocations.append(funded)
            remaining -= funded
        return allocations

    before = funded_by_bill(available_cash_cents)
    after = funded_by_bill(max(0, available_cash_cents - transaction_amount_cents))
    impacted = []
    for (bill, _), before_funded, after_funded in zip(validated, before, after):
        newly_unfunded = max(0, before_funded - after_funded)
        if newly_unfunded:
            impacted.append({"name": bill.get("name", "Bill"), "due_date": bill.get("due_date"),
                             "unfunded_cents": newly_unfunded})
    return {"requires_confirmation": bool(impacted),
            "remaining_cash_after_transaction_cents": max(0, available_cash_cents - transaction_amount_cents),
            "underfunded_bills": impacted,
            "total_bill_shortfall_cents": sum(x["unfunded_cents"] for x in impacted)}

def plan_savings_contributions(goals: Sequence[Mapping], period_starts: Sequence[str],
                               actual_contributions: Mapping[tuple[int, str], int] | None = None) -> list[dict]:
    """Plan dated and ongoing savings contributions across pay periods.

    Dated goals divide the remaining target over the remaining eligible periods
    using ceiling division. Actual contributions reduce the remaining amount;
    a missed period has no actual contribution, so a later call naturally
    spreads the unpaid amount over fewer remaining periods. Ongoing goals use
    their fixed per-period amount.
    """
    actual_contributions = actual_contributions or {}
    results = []
    for goal in goals:
        goal_id = goal.get("id")
        target = goal.get("target_amount_cents", 0)
        saved = goal.get("current_balance_cents", 0)
        ongoing = goal.get("ongoing_per_period_cents", 0)
        deadline = goal.get("target_date")
        if hasattr(deadline, "isoformat"):
            deadline = deadline.isoformat()
        for field, value in (("target_amount_cents", target),
                             ("current_balance_cents", saved),
                             ("ongoing_per_period_cents", ongoing)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        remaining = max(0, target - saved)
        goal_periods = []
        for index, period_start in enumerate(period_starts):
            actual = actual_contributions.get((goal_id, period_start), 0)
            if isinstance(actual, bool) or not isinstance(actual, int) or actual < 0:
                raise ValueError("actual contributions must be non-negative integer cents")
            remaining = max(0, remaining - actual)
            if deadline:
                if period_start > deadline or remaining == 0:
                    planned = 0
                else:
                    periods_left = sum(1 for start in period_starts[index:] if start <= deadline)
                    planned = (remaining + periods_left - 1) // periods_left if periods_left else 0
            else:
                planned = max(0, ongoing - actual)
            goal_periods.append({
                "period_start": period_start,
                "actual_contribution_cents": actual,
                "planned_contribution_cents": planned,
                "remaining_after_projected_contribution_cents": max(0, remaining - planned),
            })
            remaining = max(0, remaining - planned)
        results.append({
            "goal_id": goal_id,
            "name": goal.get("name", "Savings goal"),
            "periods": goal_periods,
            "remaining_target_cents": remaining,
            "deadline_shortfall_cents": remaining if deadline else 0,
        })
    return results

def project_debt_schedule(debts: Sequence[Mapping], periods: Sequence[Mapping[str, str]],
                          as_of_date: str) -> dict:
    """Project monthly interest and planned payments chronologically.

    Interest is posted before a payment when both fall on the same date.
    Monthly interest uses APR / 12 and integer-cent half-up rounding. A zero
    planned payment uses the saved minimum payment. Balances stop at zero;
    estimated interest is returned as forecast data only, never persisted.
    """
    from calendar import monthrange
    from datetime import date

    as_of = date.fromisoformat(as_of_date)
    parsed_periods = [
        (date.fromisoformat(p["start_date"]), date.fromisoformat(p["end_date"]), p)
        for p in periods
    ]
    if not parsed_periods:
        return {"periods": [], "debts": []}
    horizon_end = max(end for _, end, _ in parsed_periods)
    period_totals = {
        p.get("start_date"): {"debt_payments_cents": 0, "estimated_interest_cents": 0,
                              "debt_events": []}
        for _, _, p in parsed_periods
    }
    debt_results = []

    def monthly_dates(anchor_text: str | None) -> list[date]:
        if not anchor_text:
            return []
        anchor = date.fromisoformat(anchor_text)
        anchor_month = date(anchor.year, anchor.month, 1)
        as_of_month = date(as_of.year, as_of.month, 1)
        cursor = max(anchor_month, as_of_month)
        dates = []
        while cursor <= horizon_end:
            day = min(anchor.day, monthrange(cursor.year, cursor.month)[1])
            candidate = date(cursor.year, cursor.month, day)
            if as_of <= candidate <= horizon_end:
                dates.append(candidate)
            next_month = cursor.month + 1
            cursor = date(cursor.year + (next_month > 12), 1 if next_month > 12 else next_month, 1)
        return dates

    events = []
    for debt in debts:
        debt_id = debt.get("id")
        balance = debt.get("balance_cents", 0)
        minimum = debt.get("minimum_payment_cents", 0)
        planned = debt.get("planned_payment_cents", 0) or minimum
        apr = debt.get("apr_percent", 0)
        for name, value in (("balance_cents", balance), ("minimum_payment_cents", minimum),
                            ("planned_payment_cents", planned)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if isinstance(apr, bool) or not isinstance(apr, (int, float)) or apr < 0:
            raise ValueError("apr_percent must be non-negative")
        for day in monthly_dates(debt.get("interest_date")):
            events.append((day, 0, debt_id, debt, "interest"))
        for day in monthly_dates(debt.get("next_due_date")):
            events.append((day, 1, debt_id, debt, "payment"))

    balances = {debt.get("id"): debt.get("balance_cents", 0) for debt in debts}
    debt_events = []
    for day, _, debt_id, debt, event_type in sorted(events, key=lambda event: (event[0], event[1], event[2])):
        balance = balances.get(debt_id, 0)
        if balance <= 0:
            continue
        if event_type == "interest":
            basis_points = int(float(debt.get("apr_percent", 0)) * 100 + 0.5)
            denominator = 120000
            interest = (balance * basis_points + denominator // 2) // denominator
            balances[debt_id] = balance + interest
            amount = interest
        else:
            payment_amount = debt.get("planned_payment_cents", 0) or debt.get("minimum_payment_cents", 0)
            amount = min(balance, payment_amount)
            balances[debt_id] = balance - amount
        owning = next((p for start, end, p in parsed_periods if start <= day <= end), None)
        if owning is None:
            continue
        bucket = period_totals[owning.get("start_date")]
        key = "estimated_interest_cents" if event_type == "interest" else "debt_payments_cents"
        bucket[key] += amount
        event = {"debt_id": debt_id, "debt_name": debt.get("name", "Debt"),
                 "date": day.isoformat(), "type": event_type, "amount_cents": amount,
                 "balance_after_cents": balances[debt_id]}
        bucket["debt_events"].append(event)
        debt_events.append(event)

    return {
        "periods": [
            {"start_date": key, **value}
            for key, value in period_totals.items()
        ],
        "debts": [
            {"debt_id": debt.get("id"), "debt_name": debt.get("name", "Debt"),
             "projected_balance_cents": balances.get(debt.get("id"), 0)}
            for debt in debts
        ],
        "events": debt_events,
    }

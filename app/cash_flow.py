"""Weekly household cash-flow forecast from saved schedules and transactions."""
from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import database
from .household import CASH_SNAPSHOT_STALE_DAYS
from .forecast import occurrences_between

router = APIRouter()


def _date(value) -> date | None:
    return date.fromisoformat(value) if value else None


def _monthly_due_dates(day: int | None, start: date, end: date) -> list[date]:
    if not day:
        return []
    results = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        due = date(cursor.year, cursor.month, min(day, monthrange(cursor.year, cursor.month)[1]))
        if start <= due <= end:
            results.append(due)
        next_month = cursor.month + 1
        cursor = date(cursor.year + (next_month > 12), 1 if next_month > 12 else next_month, 1)
    return results


def _within_window(left: date, right: date, days: int) -> bool:
    return abs((left - right).days) <= days


def _take_matching_transaction(
    transactions: list[dict],
    used_ids: set[int],
    link_type: str,
    link_id: int,
    due_date: date,
    window_days: int,
) -> dict | None:
    candidates = [
        item for item in transactions
        if item["id"] not in used_ids
        and item["linked_obligation_type"] == link_type
        and item["linked_obligation_id"] == link_id
        and _within_window(date.fromisoformat(item["transaction_date"]), due_date, window_days)
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            abs((date.fromisoformat(item["transaction_date"]) - due_date).days),
            item["transaction_date"],
            item["id"],
        )
    )
    match = candidates[0]
    used_ids.add(match["id"])
    return match


def _take_matching_transactions(
    transactions: list[dict],
    used_ids: set[int],
    link_type: str,
    link_id: int,
    due_date: date,
    window_days: int,
) -> list[dict]:
    """Consume all linked payments eligible for one scheduled obligation cycle."""
    candidates = [
        item for item in transactions
        if item["id"] not in used_ids
        and item["transaction_type"] == "expense"
        and item["linked_obligation_type"] == link_type
        and item["linked_obligation_id"] == link_id
        and _within_window(date.fromisoformat(item["transaction_date"]), due_date, window_days)
    ]
    candidates.sort(
        key=lambda item: (
            abs((date.fromisoformat(item["transaction_date"]) - due_date).days),
            item["transaction_date"],
            item["id"],
        )
    )
    used_ids.update(item["id"] for item in candidates)
    return candidates


@router.get("/api/cash-flow/weekly")
def weekly_cash_flow(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    forecast_weeks: int = 13,
):
    """Forecast the selected window and reserve cash for later weeks' obligations."""
    if start_date is None:
        with database.connect() as db:
            settings = db.execute(
                "SELECT financial_week_start FROM household_settings WHERE id = 1"
            ).fetchone()
        weekday_name = settings["financial_week_start"] if settings else "Friday"
        weekday_index = {
            "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
            "Friday": 4, "Saturday": 5, "Sunday": 6,
        }.get(weekday_name, 4)
        today = date.today()
        start = today - timedelta(days=(today.weekday() - weekday_index) % 7)
    else:
        start = start_date
    end = end_date or (start + timedelta(days=6))
    if end < start:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    if (end - start).days > 30:
        raise HTTPException(status_code=422, detail="Cash-flow forecast window cannot exceed 31 days")
    if isinstance(forecast_weeks, bool) or not isinstance(forecast_weeks, int) or not 1 <= forecast_weeks <= 52:
        raise HTTPException(status_code=422, detail="forecast_weeks must be between 1 and 52")

    report = _weekly_cash_flow_for_window(start, end)
    buffer_target = report["buffer_target_cents"]
    current_ending_cash = (
        report["projected_cash_before_buffer_cents"] - report["funded_savings_cents"]
    )
    lowest_projected_cash = current_ending_cash
    future_forecast = []
    carried_cash = current_ending_cash
    future_start = end + timedelta(days=1)

    # Carry each period's ending cash forward. Re-read schedules and transactions
    # for every future window so each request reflects current data.
    for offset in range(1, forecast_weeks):
        future_end = future_start + timedelta(days=6)
        future = _weekly_cash_flow_for_window(
            future_start, future_end, starting_cash_override=carried_cash,
            include_savings_carryover=False,
        )
        ending_cash = (
            future["projected_cash_before_buffer_cents"] - future["funded_savings_cents"]
        )
        lowest_projected_cash = min(lowest_projected_cash, ending_cash)
        future_forecast.append({
            "start_date": future_start.isoformat(),
            "end_date": future_end.isoformat(),
            "starting_cash_cents": carried_cash,
            "expected_income_cents": future["expected_income_cents"],
            "recorded_income_cents": future["recorded_income_cents"],
            "bills_due_cents": future["bills_due_cents"],
            "minimum_debt_payments_cents": future["minimum_debt_payments_cents"],
            "transaction_expenses_cents": future["transaction_expenses_cents"],
            "projected_ending_cash_cents": ending_cash,
            "projected_shortfall_cents": max(0, -ending_cash),
            "buffer_shortfall_cents": max(0, buffer_target - ending_cash),
        })
        carried_cash = ending_cash
        future_start = future_end + timedelta(days=1)

    # The lowest balance in the entire horizon limits discretionary spending now.
    safe_to_spend = max(0, lowest_projected_cash - buffer_target)
    report["projected_ending_cash_cents"] = current_ending_cash
    report["free_to_spend_before_future_reserve_cents"] = report["free_to_spend_cents"]
    report["future_buffer_reservation_cents"] = max(
        0, report["free_to_spend_cents"] - safe_to_spend
    )
    with database.connect() as db:
        allocations = db.execute(
            """SELECT id, allocation_date, allocation_type, amount_cents,
                      target_debt_id, savings_goal_id, description, status,
                      actual_amount_cents, transaction_id
               FROM surplus_allocations
               WHERE status = 'planned'
                 AND (allocation_type <> 'buffer' OR allocation_date >= ?)
               ORDER BY allocation_date, id""",
            (start.isoformat(),),
        ).fetchall()
        allocated_cents = sum(row["amount_cents"] for row in allocations)
        debt_target = db.execute(
            """SELECT id, name, balance_cents FROM credit_cards
               WHERE active = 1 AND balance_cents > 0
               ORDER BY balance_cents ASC, id ASC LIMIT 1"""
        ).fetchone()
    safe_before_allocations = safe_to_spend
    allocation_shortfall = max(0, allocated_cents - safe_before_allocations)
    safe_to_spend = max(0, safe_to_spend - allocated_cents)
    report["safe_surplus_before_allocations_cents"] = safe_before_allocations
    report["allocation_shortfall_cents"] = allocation_shortfall
    report["planned_allocations_cents"] = allocated_cents
    report["surplus_allocations"] = [dict(row) for row in allocations]
    report["recommended_snowball_debt"] = dict(debt_target) if debt_target else None
    report["free_to_spend_cents"] = safe_to_spend
    report["extra_debt_payoff_capacity_cents"] = safe_to_spend
    report["forecast_weeks"] = forecast_weeks
    report["lowest_projected_cash_cents"] = lowest_projected_cash
    report["future_buffer_shortfall_cents"] = max(0, buffer_target - lowest_projected_cash)
    report["future_forecast"] = future_forecast
    report["notes"].append(
        "Safe-to-spend reserves cash against the lowest projected ending balance across "
        + str(forecast_weeks)
        + " forecast week(s); future weeks carry forward prior ending cash."
    )
    return report


def _weekly_cash_flow_for_window(
    start: date,
    end: date,
    starting_cash_override: int | None = None,
    include_savings_carryover: bool = True,
) -> dict:
    """Calculate one period without lookahead, optionally carrying prior cash."""
    with database.connect() as db:
        settings = db.execute(
            "SELECT starting_cash_cents, buffer_target_cents, financial_week_start FROM household_settings WHERE id = 1"
        ).fetchone()
        snapshot = db.execute(
            """SELECT effective_week_start, balance_cents, confirmed_at
               FROM cash_balance_snapshots
               WHERE effective_week_start <= ?
               ORDER BY effective_week_start DESC, id DESC LIMIT 1""",
            (start.isoformat(),),
        ).fetchone()
        any_snapshot = db.execute(
            "SELECT 1 FROM cash_balance_snapshots LIMIT 1"
        ).fetchone()
        starting_cash = (
            starting_cash_override
            if starting_cash_override is not None
            else (
                snapshot["balance_cents"] if snapshot
                else (0 if any_snapshot else (settings["starting_cash_cents"] if settings else 0))
            )
        )
        starting_cash_effective_week = snapshot["effective_week_start"] if snapshot else None
        starting_cash_confirmed_at = snapshot["confirmed_at"] if snapshot else None
        starting_cash_is_stale = True
        if snapshot:
            try:
                starting_cash_is_stale = (
                    date.today() - date.fromisoformat(snapshot["confirmed_at"][:10])
                ).days > CASH_SNAPSHOT_STALE_DAYS
            except (TypeError, ValueError):
                starting_cash_is_stale = True
        buffer_target = settings["buffer_target_cents"] if settings else 0

        # Include a short look-back for payments recorded just before the window
        # so they can mark a scheduled obligation as already covered.
        match_start = start - timedelta(days=31)
        transaction_rows = db.execute(
            """SELECT id, transaction_date, transaction_type, category, description,
                      amount_cents, linked_obligation_type, linked_obligation_id
               FROM transactions
               WHERE transaction_date >= ? AND transaction_date <= ?
               ORDER BY transaction_date, id""",
            (match_start.isoformat(), end.isoformat()),
        ).fetchall()
        all_transactions = [dict(row) for row in transaction_rows]
        in_period = [
            row for row in all_transactions
            if start.isoformat() <= row["transaction_date"] <= end.isoformat()
        ]

        # Scheduled income occurrences. A linked actual paycheck replaces its
        # matching expected occurrence, rather than being counted a second time.
        income_occurrences = []
        rows = db.execute(
            """SELECT i.id, i.name, i.net_amount_cents, i.frequency, i.next_payday,
                      p.name AS person_name
               FROM income_sources i JOIN persons p ON p.id = i.person_id
               WHERE i.active = 1 AND p.active = 1
               ORDER BY i.next_payday, i.id"""
        ).fetchall()
        for row in rows:
            anchor = date.fromisoformat(row["next_payday"])
            for payday in occurrences_between(anchor, row["frequency"], start, end):
                income_occurrences.append({
                    "source_id": row["id"],
                    "name": f'{row["person_name"]} — {row["name"]}',
                    "date": payday.isoformat(),
                    "amount_cents": row["net_amount_cents"],
                })
        income_occurrences.sort(key=lambda item: (item["date"], item["name"]))
        income_schedules = {
            row["id"]: (date.fromisoformat(row["next_payday"]), row["frequency"])
            for row in rows
        }
        used_transactions: set[int] = set()
        expected_income = []
        covered_income = []
        for item in income_occurrences:
            due_date = date.fromisoformat(item["date"])
            anchor, frequency = income_schedules[item["source_id"]]

            # A linked paycheck may be recorded up to seven days early or late,
            # but it must cover the closest scheduled occurrence for that source.
            # Otherwise a paycheck from last week could suppress this week's
            # expected income when the paydays are exactly seven days apart.
            eligible_transactions = []
            for transaction in all_transactions:
                if (
                    transaction["linked_obligation_type"] != "income"
                    or transaction["linked_obligation_id"] != item["source_id"]
                    or transaction["id"] in used_transactions
                ):
                    continue
                transaction_date = date.fromisoformat(transaction["transaction_date"])
                if not _within_window(transaction_date, due_date, 7):
                    continue
                nearby_paydays = occurrences_between(
                    anchor, frequency,
                    transaction_date - timedelta(days=7),
                    transaction_date + timedelta(days=7),
                )
                if nearby_paydays:
                    nearest_payday = min(
                        nearby_paydays,
                        key=lambda payday: (abs((payday - transaction_date).days), payday),
                    )
                    if nearest_payday == due_date:
                        eligible_transactions.append(transaction)

            match = _take_matching_transaction(
                eligible_transactions, used_transactions, "income", item["source_id"],
                due_date, 7,
            )
            if match:
                covered_income.append({
                    **item, "covered_by_transaction_id": match["id"],
                    "transaction_date": match["transaction_date"],
                })
            else:
                expected_income.append(item)

        # Bills are expanded only within the selected window. An explicitly
        # linked transaction suppresses one occurrence when it falls within the
        # bill's configured flexible window (at least seven days).
        bills = []
        rows = db.execute(
            """SELECT id, name, amount_cents, next_due_date, frequency,
                      flexible_window_days, category
               FROM bills WHERE active = 1 ORDER BY next_due_date, id"""
        ).fetchall()
        for row in rows:
            anchor = _date(row["next_due_date"])
            if anchor is None:
                continue
            for due in occurrences_between(anchor, row["frequency"], start, end):
                match = _take_matching_transaction(
                    all_transactions, used_transactions, "bill", row["id"], due,
                    max(7, row["flexible_window_days"] or 0),
                )
                bills.append({
                    "id": row["id"], "name": row["name"], "category": row["category"],
                    "due_date": due.isoformat(), "amount_cents": row["amount_cents"],
                    "status": "recorded" if match else "due",
                    "covered_by_transaction_id": match["id"] if match else None,
                    "transaction_date": match["transaction_date"] if match else None,
                })
        bills.sort(key=lambda item: (item["due_date"], item["name"]))

        debts = []
        rows = db.execute(
            """SELECT id, name, balance_cents, minimum_payment_cents,
                      next_due_date, due_day_of_month
               FROM credit_cards WHERE active = 1 ORDER BY id"""
        ).fetchall()
        for row in rows:
            anchor = _date(row["next_due_date"])
            due_dates = (
                occurrences_between(anchor, "monthly", start, end)
                if anchor else _monthly_due_dates(row["due_day_of_month"], start, end)
            )
            capped_minimum = min(row["minimum_payment_cents"], row["balance_cents"])
            for due in due_dates:
                matches = _take_matching_transactions(
                    all_transactions, used_transactions, "debt", row["id"], due, 7,
                )
                qualifying_paid = sum(abs(item["amount_cents"]) for item in matches)
                paid_toward_minimum = min(capped_minimum, qualifying_paid)
                remaining_minimum = max(0, capped_minimum - paid_toward_minimum)
                debts.append({
                    "id": row["id"], "name": row["name"], "due_date": due.isoformat(),
                    "original_minimum_payment_cents": row["minimum_payment_cents"],
                    "balance_capped_minimum_cents": capped_minimum,
                    "qualifying_payments_cents": qualifying_paid,
                    "paid_toward_minimum_cents": paid_toward_minimum,
                    "extra_payment_cents": max(0, qualifying_paid - capped_minimum),
                    "amount_cents": remaining_minimum,
                    "status": (
                        "recorded" if remaining_minimum == 0 and qualifying_paid > 0
                        else "partial" if qualifying_paid > 0
                        else "due"
                    ),
                    "covered_by_transaction_ids": [item["id"] for item in matches],
                    "covered_by_transaction_id": matches[0]["id"] if matches else None,
                    "transaction_date": matches[0]["transaction_date"] if len(matches) == 1 else None,
                    "transaction_dates": [item["transaction_date"] for item in matches],
                })
        debts.sort(key=lambda item: (item["due_date"], item["name"]))

        goals = db.execute(
            """SELECT id, name, target_amount_cents, current_balance_cents, target_date,
                      weekly_target_cents, schedule_start_date, created_at, priority
               FROM savings_goals WHERE active = 1
               ORDER BY priority, name COLLATE NOCASE, id"""
        ).fetchall()
        savings_history = db.execute(
            """SELECT a.savings_goal_id, t.transaction_date, COALESCE(a.actual_amount_cents, 0) AS amount_cents
               FROM surplus_allocations a
               JOIN transactions t ON t.id = a.transaction_id
               WHERE a.allocation_type = 'savings' AND a.status = 'recorded'
                 AND a.savings_goal_id IS NOT NULL
               ORDER BY t.transaction_date, a.id"""
        ).fetchall()

    # Recorded cash movements are counted by transaction type, never by category
    # or sign alone. Transfers between household accounts are cash-neutral.
    transaction_income = sum(
        abs(row["amount_cents"]) for row in in_period
        if row["transaction_type"] == "income"
    )
    transaction_expenses = sum(
        abs(row["amount_cents"]) for row in in_period
        if row["transaction_type"] == "expense"
    )
    transaction_transfers = [
        {
            "id": row["id"], "date": row["transaction_date"],
            "description": row["description"], "amount_cents": row["amount_cents"],
        }
        for row in in_period if row["transaction_type"] == "transfer"
    ]

    spending_by_category = {}
    for row in in_period:
        if row["transaction_type"] == "expense":
            category = row["category"]
            spending_by_category[category] = spending_by_category.get(category, 0) + abs(row["amount_cents"])
    category_spending = [
        {"category": category, "amount_cents": amount}
        for category, amount in sorted(
            spending_by_category.items(), key=lambda pair: (-pair[1], pair[0].casefold())
        )
    ]

    uncovered_bills = [item for item in bills if item["status"] == "due"]
    uncovered_debts = [item for item in debts if item["amount_cents"] > 0]
    expected_income_cents = sum(item["amount_cents"] for item in expected_income)
    bills_due_cents = sum(item["amount_cents"] for item in uncovered_bills)
    minimum_debt_cents = sum(item["amount_cents"] for item in uncovered_debts)

    # Every active savings goal is budgeted per financial week, independently
    # of paydays. Dated goals derive their weekly target from the deadline;
    # undated goals use the user-entered weekly amount for ongoing savings.
    # Missed weekly targets carry forward, and recorded contributions reduce arrears.
    savings_plan = []
    planned_savings = 0
    history_by_goal: dict[int, list[tuple[date, int]]] = {}
    for entry in savings_history:
        history_by_goal.setdefault(entry["savings_goal_id"], []).append(
            (date.fromisoformat(entry["transaction_date"]), entry["amount_cents"])
        )
    for goal in goals:
        has_deadline = bool(goal["target_date"])
        remaining = max(0, goal["target_amount_cents"] - goal["current_balance_cents"])
        target_date = date.fromisoformat(goal["target_date"]) if has_deadline else None
        schedule_start = date.fromisoformat(goal["schedule_start_date"] or goal["created_at"][:10])
        weekly_target = goal["weekly_target_cents"] or 0
        if has_deadline and weekly_target == 0 and remaining > 0 and target_date >= start:
            weeks_left = max(1, ((target_date - start).days // 7) + 1)
            weekly_target = (remaining + weeks_left - 1) // weeks_left
        elapsed_weeks = max(0, (start - schedule_start).days // 7)
        history = history_by_goal.get(goal["id"], [])
        saved_before = sum(amount for day, amount in history if day < start)
        saved_this_week = sum(amount for day, amount in history if start <= day <= end)
        arrears = max(0, weekly_target * elapsed_weeks - saved_before) if include_savings_carryover else 0

        # A goal with a date stops reserving new weekly deposits after its
        # deadline. An undated goal with a positive amount stops once funded;
        # amount=0 means ongoing general savings with no finish line.
        if has_deadline and target_date < start:
            due_this_week = 0
        else:
            due_this_week = max(0, weekly_target + arrears - saved_this_week)
            if goal["target_amount_cents"] > 0:
                due_this_week = min(remaining, due_this_week)

        if has_deadline:
            weeks_remaining = max(1, ((target_date - start).days // 7) + 1) if target_date >= start else 0
            required_weekly = (remaining + weeks_remaining - 1) // weeks_remaining if weeks_remaining else remaining
            behind_schedule = bool(target_date < start or required_weekly > weekly_target)
        else:
            required_weekly = None
            behind_schedule = False

        savings_plan.append({
            "goal_id": goal["id"], "goal_name": goal["name"],
            "weekly_target_cents": weekly_target, "arrears_cents": arrears,
            "planned_this_week_cents": due_this_week,
            "recorded_this_week_cents": saved_this_week,
            "remaining_cents": remaining if goal["target_amount_cents"] > 0 else None,
            "target_date": goal["target_date"],
            "required_weekly_to_deadline_cents": required_weekly,
            "behind_schedule": behind_schedule,
        })
        planned_savings += due_this_week

    projected_cash = (
        starting_cash + expected_income_cents + transaction_income
        - bills_due_cents - minimum_debt_cents - transaction_expenses
    )
    buffer_replenishment = max(0, buffer_target - projected_cash)
    available_after_buffer = max(0, projected_cash - buffer_target)
    funded_savings = min(planned_savings, available_after_buffer)
    savings_shortfall = planned_savings - funded_savings
    remaining_savings_capacity = funded_savings
    for item in savings_plan:
        item["funded_this_week_cents"] = min(
            item["planned_this_week_cents"], remaining_savings_capacity
        )
        item["unfunded_this_week_cents"] = (
            item["planned_this_week_cents"] - item["funded_this_week_cents"]
        )
        remaining_savings_capacity -= item["funded_this_week_cents"]
        item["deadline_warning"] = bool(
            item["behind_schedule"] or item["unfunded_this_week_cents"] > 0
        )
    free_to_spend = max(0, available_after_buffer - funded_savings)

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "financial_week_start": (settings["financial_week_start"] if settings and "financial_week_start" in settings.keys() else "Friday"),
        "starting_cash_cents": starting_cash,
        "starting_cash_effective_week": starting_cash_effective_week,
        "starting_cash_confirmed_at": starting_cash_confirmed_at,
        "starting_cash_is_stale": starting_cash_is_stale,
        "buffer_target_cents": buffer_target,
        "expected_income": expected_income,
        "expected_income_cents": expected_income_cents,
        "recorded_income_cents": transaction_income,
        "covered_income": covered_income,
        "bills": bills,
        "bills_due_cents": bills_due_cents,
        "bills_covered_by_transactions_cents": sum(
            item["amount_cents"] for item in bills if item["status"] == "recorded"
        ),
        "minimum_debt_payments": debts,
        "minimum_debt_payments_cents": minimum_debt_cents,
        "debt_payments_covered_by_transactions_cents": sum(
            item["paid_toward_minimum_cents"] for item in debts
        ),
        "debt_extra_payments_by_transactions_cents": sum(
            item["extra_payment_cents"] for item in debts
        ),
        "transactions": [
            {
                "id": row["id"], "date": row["transaction_date"],
                "type": row["transaction_type"], "category": row["category"],
                "description": row["description"], "amount_cents": row["amount_cents"],
                "linked_obligation_type": row["linked_obligation_type"],
                "linked_obligation_id": row["linked_obligation_id"],
            }
            for row in in_period
        ],
        "transaction_expenses_cents": transaction_expenses,
        "category_spending": category_spending,
        "transfers": transaction_transfers,
        "planned_savings_cents": planned_savings,
        "savings_plan": savings_plan,
        "funded_savings_cents": funded_savings,
        "savings_shortfall_cents": savings_shortfall,
        "projected_cash_before_buffer_cents": projected_cash,
        "buffer_replenishment_cents": buffer_replenishment,
        "projected_shortfall_cents": max(0, -projected_cash),
        "buffer_shortfall_cents": max(0, buffer_target - projected_cash),
        "free_to_spend_cents": free_to_spend,
        "extra_debt_payoff_capacity_cents": free_to_spend,
        "notes": [
            "All savings goals use weekly targets and carry forward missed amounts based on recorded contributions. Goals without a target date are ongoing weekly savings.",
            "Only active scheduled income, bills, debts, and savings goals are included.",
            "Link a recorded transaction to the scheduled item it paid or received to prevent duplicate counting.",
            "Linked transactions match scheduled occurrences within seven days, or the bill's larger flexible window.",
            "Linked debt payments reduce that cycle's minimum by the amount paid; payments above the minimum are shown as extra debt reduction.",
            "Debt minimums are capped at the recorded outstanding balance. Payment matching is a planning assumption and may differ from lender payment-application rules.",
            "Transfers are listed for reference but do not change household cash totals.",
            "All monetary calculations use integer cents; this report never initiates payments or transfers.",
            "Starting cash is a confirmed household snapshot effective at the beginning of its financial week; snapshots are not automatically inferred from transactions.",
            (
                "WARNING: no confirmed starting-cash snapshot is available for this week. Set cash on hand in Household & income."
                if starting_cash_confirmed_at is None
                else f"WARNING: starting cash was last confirmed more than {CASH_SNAPSHOT_STALE_DAYS} days ago. Reconfirm it in Household & income."
            ) if starting_cash_is_stale else "Starting cash confirmation is current.",
        ],
    }


class SurplusAllocationCreate(BaseModel):
    allocation_type: str = Field(pattern=r"^(debt|buffer|savings|unallocated)$")
    amount_cents: int = Field(gt=0)
    allocation_date: date = Field(default_factory=date.today)
    target_debt_id: int | None = Field(default=None, gt=0)
    savings_goal_id: int | None = Field(default=None, gt=0)
    description: str = Field(default="", max_length=200)


class DebtPaymentRecord(BaseModel):
    actual_amount_cents: int = Field(gt=0)
    transaction_date: date = Field(default_factory=date.today)
    description: str = Field(default="Debt payment recorded", max_length=200)


@router.get("/api/surplus-allocations")
def list_surplus_allocations():
    with database.connect() as db:
        rows = db.execute(
            """SELECT a.id, a.allocation_date, a.allocation_type, a.amount_cents,
                      a.target_debt_id, c.name AS target_debt_name,
                      a.savings_goal_id, g.name AS savings_goal_name,
                      a.description, a.status, a.actual_amount_cents, a.transaction_id
               FROM surplus_allocations a
               LEFT JOIN credit_cards c ON c.id = a.target_debt_id
               LEFT JOIN savings_goals g ON g.id = a.savings_goal_id
               ORDER BY CASE a.status WHEN 'planned' THEN 0 ELSE 1 END, a.allocation_date DESC, a.id DESC
               LIMIT 100"""
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("/api/surplus-allocations", status_code=status.HTTP_201_CREATED)
def create_surplus_allocation(body: SurplusAllocationCreate):
    target_debt_id = body.target_debt_id
    if body.allocation_type == "debt" and target_debt_id is None:
        with database.connect() as db:
            target = db.execute(
                "SELECT id FROM credit_cards WHERE active = 1 AND balance_cents > 0 ORDER BY balance_cents ASC, id ASC LIMIT 1"
            ).fetchone()
        if target is None:
            raise HTTPException(status_code=422, detail="Add an active debt before allocating a snowball payment")
        target_debt_id = target["id"]
    if body.allocation_type != "debt" and target_debt_id is not None:
        raise HTTPException(status_code=422, detail="A debt target is only valid for debt allocations")
    if body.allocation_type == "savings" and body.savings_goal_id is None:
        raise HTTPException(status_code=422, detail="Choose a savings goal for this allocation")
    if body.allocation_type != "savings" and body.savings_goal_id is not None:
        raise HTTPException(status_code=422, detail="A savings goal is only valid for savings allocations")

    # Planned allocations are already deducted from this look-ahead forecast.
    report = weekly_cash_flow(start_date=None, end_date=None, forecast_weeks=13)
    if body.amount_cents > report["free_to_spend_cents"]:
        raise HTTPException(
            status_code=422,
            detail=f"Allocation exceeds safe available surplus ({report['free_to_spend_cents']} cents). Refresh the forecast and choose a smaller amount.",
        )
    with database.connect() as db:
        if target_debt_id is not None and db.execute(
            "SELECT id FROM credit_cards WHERE id = ? AND active = 1", (target_debt_id,)
        ).fetchone() is None:
            raise HTTPException(status_code=422, detail="Selected debt is not active")
        if body.savings_goal_id is not None and db.execute(
            "SELECT id FROM savings_goals WHERE id = ? AND active = 1", (body.savings_goal_id,)
        ).fetchone() is None:
            raise HTTPException(status_code=422, detail="Selected savings goal is not active")
        cursor = db.execute(
            """INSERT INTO surplus_allocations
               (allocation_date, allocation_type, amount_cents, target_debt_id,
                savings_goal_id, description)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (body.allocation_date.isoformat(), body.allocation_type, body.amount_cents,
             target_debt_id, body.savings_goal_id, body.description.strip()),
        )
        row = db.execute(
            "SELECT * FROM surplus_allocations WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return dict(row)


@router.post("/api/surplus-allocations/{allocation_id}/record-debt-payment")
def record_allocated_debt_payment(allocation_id: int, body: DebtPaymentRecord):
    with database.connect() as db:
        allocation = db.execute(
            """SELECT * FROM surplus_allocations
               WHERE id = ? AND allocation_type = 'debt' AND status = 'planned'""",
            (allocation_id,),
        ).fetchone()
        if allocation is None:
            raise HTTPException(status_code=404, detail="Planned debt allocation not found")
        if body.actual_amount_cents > allocation["amount_cents"]:
            raise HTTPException(status_code=422, detail="Actual payment cannot exceed the allocated amount. Refresh the forecast and create a new allocation for any additional payment.")
        debt = db.execute(
            "SELECT id, name, balance_cents FROM credit_cards WHERE id = ?",
            (allocation["target_debt_id"],),
        ).fetchone()
        if debt is None:
            raise HTTPException(status_code=404, detail="Target debt no longer exists")
        cursor = db.execute(
            """INSERT INTO transactions
               (transaction_date, category, transaction_type, description, amount_cents, notes)
               VALUES (?, 'debt payment', 'expense', ?, ?, ?)""",
            (body.transaction_date.isoformat(),
             body.description.strip() or ("Extra payment toward " + debt["name"]),
             body.actual_amount_cents,
             "Extra debt snowball payment for " + debt["name"] +
             "; recorded from surplus allocation #" + str(allocation_id) +
             ". Kept separate from minimum-payment matching."),
        )
        transaction_id = cursor.lastrowid
        db.execute(
            "UPDATE credit_cards SET balance_cents = MAX(0, balance_cents - ?) WHERE id = ?",
            (body.actual_amount_cents, debt["id"]),
        )
        db.execute(
            """UPDATE surplus_allocations
               SET status = 'recorded', actual_amount_cents = ?, transaction_id = ?
               WHERE id = ?""",
            (body.actual_amount_cents, transaction_id, allocation_id),
        )
    return {"allocation_id": allocation_id, "transaction_id": transaction_id,
            "actual_amount_cents": body.actual_amount_cents, "status": "recorded"}


@router.post("/api/surplus-allocations/{allocation_id}/record-savings-contribution")
def record_allocated_savings_contribution(allocation_id: int, body: DebtPaymentRecord):
    with database.connect() as db:
        allocation = db.execute(
            """SELECT * FROM surplus_allocations
               WHERE id = ? AND allocation_type = 'savings' AND status = 'planned'""",
            (allocation_id,),
        ).fetchone()
        if allocation is None:
            raise HTTPException(status_code=404, detail="Planned savings allocation not found")
        if body.actual_amount_cents > allocation["amount_cents"]:
            raise HTTPException(status_code=422, detail="Actual contribution cannot exceed the allocated amount.")
        goal = db.execute(
            "SELECT id, name FROM savings_goals WHERE id = ? AND active = 1",
            (allocation["savings_goal_id"],),
        ).fetchone()
        if goal is None:
            raise HTTPException(status_code=404, detail="Target savings goal is no longer active")
        cursor = db.execute(
            """INSERT INTO transactions
               (transaction_date, category, transaction_type, description, amount_cents, notes)
               VALUES (?, 'savings contribution', 'expense', ?, ?, ?)""",
            (body.transaction_date.isoformat(),
             body.description.strip() or ("Contribution to " + goal["name"]),
             body.actual_amount_cents,
             "Recorded from surplus allocation #" + str(allocation_id)),
        )
        transaction_id = cursor.lastrowid
        db.execute(
            "UPDATE savings_goals SET current_balance_cents = current_balance_cents + ? WHERE id = ?",
            (body.actual_amount_cents, goal["id"]),
        )
        db.execute(
            """UPDATE surplus_allocations
               SET status = 'recorded', actual_amount_cents = ?, transaction_id = ?
               WHERE id = ?""",
            (body.actual_amount_cents, transaction_id, allocation_id),
        )
    return {"allocation_id": allocation_id, "transaction_id": transaction_id,
            "actual_amount_cents": body.actual_amount_cents, "status": "recorded"}


@router.post("/api/surplus-allocations/{allocation_id}/cancel")
def cancel_surplus_allocation(allocation_id: int):
    with database.connect() as db:
        cursor = db.execute(
            "UPDATE surplus_allocations SET status = 'cancelled' WHERE id = ? AND status = 'planned'",
            (allocation_id,),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Planned allocation not found")
    return {"id": allocation_id, "status": "cancelled"}


@router.get("/weekly", response_class=HTMLResponse)
def weekly_report_page():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weekly Cash Flow · CashFlow Engine</title>
<style>
:root{font-family:system-ui,sans-serif;color:#172033;background:#f3f6fa}*{box-sizing:border-box}
body{margin:0;padding:24px 14px 48px}main{max-width:1150px;margin:auto}a{color:#0e7490;font-weight:650;text-decoration:none}
h1{color:#155e75;margin:8px 0}h2{font-size:1.1rem;margin:0 0 12px}.muted{color:#5c687a;line-height:1.5}
.toolbar,.card{background:white;padding:20px;border-radius:15px;box-shadow:0 8px 28px #172b4d0d;margin-top:16px}
.toolbar{display:flex;gap:12px;align-items:end;flex-wrap:wrap}.field{flex:1;min-width:150px}
label{display:block;font-size:.9rem;font-weight:650;margin-bottom:5px}input,select{width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font:inherit}
button{border:0;border-radius:8px;padding:10px 14px;background:#0e7490;color:white;font-weight:700;cursor:pointer}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:10px;margin-top:16px}
.metric{background:white;padding:15px;border-radius:12px;box-shadow:0 5px 18px #172b4d0a;min-width:0}
.metric span{display:block;color:#5c687a;font-size:.88rem}.metric strong{display:block;font-size:1.25rem;color:#155e75;margin-top:5px;overflow-wrap:anywhere}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:16px}
.list{padding:0;list-style:none;margin:0}.list li{padding:12px 0;border-top:1px solid #e2e8f0;overflow-wrap:anywhere}
.list li:first-child{border-top:0}.item-title{font-weight:700}.detail{font-size:.9rem;color:#5c687a;margin-top:4px}
.status{padding:12px;border-radius:9px;background:#e0f2fe;color:#075985;margin-top:16px}.warning{background:#fff7ed;color:#9a3412}.error{background:#fee2e2;color:#991b1b}.status[hidden]{display:none}
</style></head><body><main><a href="/">← Dashboard</a><h1>Weekly Cash Flow</h1>
<p class="muted">Review what is expected, what is already recorded, and what remains after required payments, the household buffer, and planned savings. Forecasts are estimates and never move money.</p>
<div class="toolbar"><div class="field"><label for="week-start">Start of financial week</label><select id="week-start"><option>Monday</option><option>Tuesday</option><option>Wednesday</option><option>Thursday</option><option selected>Friday</option><option>Saturday</option><option>Sunday</option></select></div><button id="save-week-start" type="button">Save week start</button><div class="field"><label for="start">Start date</label><input id="start" type="date" required></div>
<div class="field"><label for="end">End date</label><input id="end" type="date" required></div><button id="refresh" type="button">Update report</button></div>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<div id="metrics" class="metrics"></div>
<section class="card"><h2>Confirm starting cash</h2>
<p class="muted">Confirm the household cash balance at the start of the current financial week. An estimate is based only on recorded transactions since the previous snapshot; verify it against actual cash. Confirming a balance does not change or delete transactions.</p>
<p id="weekly-cash-estimate" class="muted" role="status">Checking whether an estimate is available…</p>
<button id="weekly-cash-use-estimate" type="button" hidden>Use estimated balance (not saved)</button>
<form id="weekly-cash-form" class="toolbar">
<div class="field"><label for="weekly-cash-amount">Cash on hand ($)</label><input id="weekly-cash-amount" type="number" min="0" step="0.01" required></div>
<div class="field"><label for="weekly-cash-week">Effective week starts</label><input id="weekly-cash-week" type="date" required></div>
<button type="submit">Confirm cash balance</button>
</form>
<p id="weekly-cash-status" class="muted" role="status">Loading cash confirmation…</p>
</section>
<section class="card"><h2>Allocate safe available money</h2>
<p class="muted">The forecast reports the amount you can allocate without dropping below the household buffer in any forecast week. You decide where it goes; planned allocations reduce what remains available. The default debt suggestion follows the snowball method.</p>
<p id="snowball-suggestion" class="muted">Loading debt recommendation…</p>
<form id="allocation-form" class="toolbar">
<div class="field"><label for="allocation-type">Use money for</label><select id="allocation-type"><option value="debt">Extra debt payment (snowball)</option><option value="buffer">Add to household buffer</option><option value="savings">Savings goal</option><option value="unallocated">Leave unallocated</option></select></div>
<div class="field"><label for="allocation-amount">Amount ($)</label><input id="allocation-amount" type="number" min="0.01" step="0.01" required></div>
<div class="field" id="debt-field"><label for="allocation-debt">Debt target</label><select id="allocation-debt"></select></div>
<div class="field" id="savings-field" hidden><label for="allocation-savings">Savings goal</label><select id="allocation-savings"></select></div>
<div class="field"><label for="allocation-description">Note (optional)</label><input id="allocation-description" maxlength="200" placeholder="What is this for?"></div>
<button type="submit">Save allocation</button></form>
<h2 style="margin-top:20px">Your allocations</h2><ul id="allocations" class="list"><li class="muted">Loading allocations…</li></ul>
</section>
<div class="grid">
<section class="card"><h2>Expected income</h2><ul id="income" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Bills due</h2><ul id="bills" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Minimum debt payments</h2><ul id="debts" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Recorded transactions</h2><ul id="transactions" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Spending by category</h2><ul id="category-spending" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Planned savings</h2><div id="savings" class="muted">Loading…</div></section>
<section class="card"><h2>Upcoming weeks & buffer protection</h2><ul id="future-forecast" class="list"><li class="muted">Loading future forecast…</li></ul></section>
<section class="card"><h2>Forecast assumptions</h2><ul id="notes" class="list"></ul></section>
</div></main>
<script>
const statusBox=document.getElementById("status");
function dollars(cents){const sign=cents<0?"−":"";return sign+new Intl.NumberFormat(undefined,{style:"currency",currency:"USD"}).format(Math.abs(cents)/100);}
function el(tag,cls,txt){const n=document.createElement(tag);if(cls)n.className=cls;if(txt!==undefined)n.textContent=txt;return n;}
async function api(url){const r=await fetch(url);const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"Could not load the weekly forecast.");return d;}
async function post(url,body){const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"Could not save the allocation.");return d;}
function message(text,kind=""){statusBox.textContent=text;statusBox.className="status"+(kind?" "+kind:"");statusBox.hidden=false;}
function renderList(id,items,empty,make){const root=document.getElementById(id);root.replaceChildren();if(!items.length){root.appendChild(el("li","muted",empty));return;}for(const item of items){const li=el("li","");make(li,item);root.appendChild(li);}}
function metric(root,label,value){const card=el("div","metric");card.appendChild(el("span","",label));card.appendChild(el("strong","",value));root.appendChild(card);}
async function load(){const start=document.getElementById("start").value;const end=document.getElementById("end").value;
if(!start||!end)throw Error("Choose both report dates.");
const report=await api("/api/cash-flow/weekly?start_date="+encodeURIComponent(start)+"&end_date="+encodeURIComponent(end));
const metrics=document.getElementById("metrics");metrics.replaceChildren();
for(const [label,value] of [
["Starting cash",report.starting_cash_cents],["Expected income",report.expected_income_cents],
["Recorded income",report.recorded_income_cents],["Bills still due",report.bills_due_cents],
["Minimum debt payments",report.minimum_debt_payments_cents],["Recorded expenses",report.transaction_expenses_cents],
["Projected cash before buffer",report.projected_cash_before_buffer_cents],["Buffer target",report.buffer_target_cents],
["Buffer replenishment needed",report.buffer_replenishment_cents],["Planned savings",report.planned_savings_cents],
["Savings funded",report.funded_savings_cents],
["Available before future reserve",report.free_to_spend_before_future_reserve_cents],
["Reserved for future bills",report.future_buffer_reservation_cents],
["Safe available surplus",report.free_to_spend_cents],
["Already allocated",report.planned_allocations_cents],
["Still available to allocate",report.free_to_spend_cents]
])metric(metrics,label,dollars(value));
if(report.projected_shortfall_cents>0)message("Projected shortfall: "+dollars(report.projected_shortfall_cents)+". Required obligations exceed projected cash.","warning");
else if(report.buffer_shortfall_cents>0)message("Projected cash is below the household buffer target by "+dollars(report.buffer_shortfall_cents)+". Extra spending and savings are not available until the buffer is restored.","warning");
else if(report.future_buffer_shortfall_cents>0)message("Future weeks would fall below the household buffer by as much as "+dollars(report.future_buffer_shortfall_cents)+". Safe-to-spend has been reduced to $0 until the forecast gap is addressed.","warning");
else if(report.allocation_shortfall_cents>0)message("Your planned allocations now exceed safe available surplus by "+dollars(report.allocation_shortfall_cents)+". Review or cancel planned allocations before making more payments.","warning");
else if(report.future_buffer_reservation_cents>0)message(dollars(report.future_buffer_reservation_cents)+" is reserved for upcoming obligations so the projected household balance can stay above its buffer.","warning");
else if(report.savings_shortfall_cents>0)message("Required obligations and the buffer are covered, but planned savings are short by "+dollars(report.savings_shortfall_cents)+".","warning");
else {statusBox.hidden=true;}
renderList("income",report.expected_income,"No scheduled income remains expected in this window.",(li,x)=>{li.appendChild(el("div","item-title",x.name+" · "+dollars(x.amount_cents)));li.appendChild(el("div","detail",x.date));});
const billRows=report.bills.map(x=>({...x,kind:"bill"}));
renderList("bills",billRows,"No bills due in this window.",(li,x)=>{li.appendChild(el("div","item-title",x.name+" · "+dollars(x.amount_cents)));li.appendChild(el("div","detail",x.due_date+" · "+x.status+(x.transaction_date?" · recorded "+x.transaction_date:"")));});
renderList("debts",report.minimum_debt_payments,"No minimum debt payments due in this window.",(li,x)=>{li.appendChild(el("div","item-title",x.name+" · "+dollars(x.amount_cents)+" remaining"));const detail=[x.due_date,x.status];if(x.qualifying_payments_cents)detail.push(dollars(x.paid_toward_minimum_cents)+" toward minimum");if(x.extra_payment_cents)detail.push(dollars(x.extra_payment_cents)+" extra");if(x.transaction_dates&&x.transaction_dates.length)detail.push("recorded "+x.transaction_dates.join(", "));li.appendChild(el("div","detail",detail.join(" · ")));});
renderList("transactions",report.transactions,"No transactions recorded in this window.",(li,x)=>{li.appendChild(el("div","item-title",x.description+" · "+dollars(x.amount_cents)));li.appendChild(el("div","detail",x.date+" · "+x.type+" · "+x.category+(x.linked_obligation_type?" · linked "+x.linked_obligation_type:"")));});
renderList("category-spending",report.category_spending,"No expense transactions recorded in this window.",(li,x)=>{li.appendChild(el("div","item-title",x.category.split(" ").map(w=>w.charAt(0).toUpperCase()+w.slice(1)).join(" ")+" · "+dollars(x.amount_cents)));});
const savings=document.getElementById("savings");savings.replaceChildren();savings.appendChild(el("p","", "Planned: "+dollars(report.planned_savings_cents)));savings.appendChild(el("p","", "Funded after obligations and buffer: "+dollars(report.funded_savings_cents)));savings.appendChild(el("p","", "Not covered by this forecast: "+dollars(report.savings_shortfall_cents)));savings.appendChild(el("p","muted","Contributions are estimated from active savings goals and distinct scheduled payday dates."));
renderList("notes",report.notes,"No additional assumptions.",(li,note)=>li.appendChild(el("div","detail",note)));
const recommendation=report.recommended_snowball_debt;
document.getElementById("snowball-suggestion").textContent=recommendation
 ? "Suggested snowball target: "+recommendation.name+" ("+dollars(recommendation.balance_cents)+" remaining). This is a suggestion only; you choose the allocation."
 : "Add an active debt to receive a snowball recommendation.";
const debtSelect=document.getElementById("allocation-debt");
const goalSelect=document.getElementById("allocation-savings");
const [debts,goals,allocations]=await Promise.all([
 api("/api/debts"),api("/api/savings-goals"),api("/api/surplus-allocations")
]);
debtSelect.replaceChildren();for(const debt of debts.filter(x=>x.active&&x.balance_cents>0)){const option=el("option","",debt.name+" — "+dollars(debt.balance_cents));option.value=debt.id;debtSelect.appendChild(option);}
if(recommendation)debtSelect.value=String(recommendation.id);
goalSelect.replaceChildren();for(const goal of goals.filter(x=>x.active)){const option=el("option","",goal.name);option.value=goal.id;goalSelect.appendChild(option);}
const typeSelect=document.getElementById("allocation-type");
function updateAllocationFields(){document.getElementById("debt-field").hidden=typeSelect.value!=="debt";document.getElementById("savings-field").hidden=typeSelect.value!=="savings";}
typeSelect.onchange=updateAllocationFields;updateAllocationFields();
renderList("allocations",allocations.filter(a=>a.status!=="planned"||a.allocation_type!=="buffer"||a.allocation_date>=report.start_date),"No allocations yet. Choose an amount above to reserve part of your safe surplus.",(li,a)=>{
 const label={debt:"Debt snowball",buffer:"Household buffer",savings:"Savings goal",unallocated:"Leave unallocated"}[a.allocation_type]||a.allocation_type;
 li.appendChild(el("div","item-title",label+" · "+dollars(a.amount_cents)+" · "+a.status));
 li.appendChild(el("div","detail",(a.target_debt_name||a.savings_goal_name||a.description||"No note")+" · "+a.allocation_date+(a.actual_amount_cents!==null&&a.actual_amount_cents!==undefined?" · actually paid "+dollars(a.actual_amount_cents):"")));
 if(a.status==="planned"){
   const actions=el("div","toolbar","");
   if(a.allocation_type==="debt"){
     const record=el("button","","Record actual debt payment");record.type="button";
     record.onclick=async()=>{const input=prompt("Enter the actual payment in dollars (up to "+(a.amount_cents/100).toFixed(2)+"):",(a.amount_cents/100).toFixed(2));if(input===null)return;const amount=Math.round(Number(input)*100);if(!Number.isFinite(amount)||amount<=0){message("Enter a valid positive payment amount.","error");return;}try{await post("/api/surplus-allocations/"+a.id+"/record-debt-payment",{actual_amount_cents:amount,transaction_date:iso(new Date()),description:"Extra debt snowball payment"});message("Actual debt payment recorded. The debt balance and forecast have been updated.");await load();}catch(e){message(e.message,"error");}};
     actions.appendChild(record);
   }
   if(a.allocation_type==="savings"){
     const record=el("button","","Record actual savings contribution");record.type="button";
     record.onclick=async()=>{const input=prompt("Enter the actual contribution in dollars (up to "+(a.amount_cents/100).toFixed(2)+"):",(a.amount_cents/100).toFixed(2));if(input===null)return;const amount=Math.round(Number(input)*100);if(!Number.isFinite(amount)||amount<=0){message("Enter a valid positive contribution amount.","error");return;}try{await post("/api/surplus-allocations/"+a.id+"/record-savings-contribution",{actual_amount_cents:amount,transaction_date:iso(new Date()),description:"Savings goal contribution"});message("Savings contribution recorded. The goal balance and forecast have been updated.");await load();}catch(e){message(e.message,"error");}};
     actions.appendChild(record);
   }
   const cancel=el("button","","Cancel allocation");cancel.type="button";cancel.onclick=async()=>{try{await post("/api/surplus-allocations/"+a.id+"/cancel",{});message("Allocation cancelled and surplus released.");await load();}catch(e){message(e.message,"error");}};
   actions.appendChild(cancel);li.appendChild(actions);
 }
});
renderList("future-forecast",report.future_forecast,"No future weeks included in this forecast.",(li,x)=>{
  li.appendChild(el("div","item-title",x.start_date+" to "+x.end_date+" · ending cash "+dollars(x.projected_ending_cash_cents)));
  li.appendChild(el("div","detail","Starting cash "+dollars(x.starting_cash_cents)+" · income "+dollars(x.expected_income_cents+x.recorded_income_cents)+" · bills "+dollars(x.bills_due_cents)+" · minimum debt "+dollars(x.minimum_debt_payments_cents)));
  if(x.buffer_shortfall_cents>0)li.appendChild(el("div","detail","Buffer shortfall: "+dollars(x.buffer_shortfall_cents)));
}); 
}
function iso(d){return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0");}
function alignToWeekStart(dayName){const index={Monday:0,Tuesday:1,Wednesday:2,Thursday:3,Friday:4,Saturday:5,Sunday:6}[dayName]??4;const today=new Date();today.setHours(12,0,0,0);today.setDate(today.getDate()-((today.getDay()+6-index)%7));document.getElementById("start").value=iso(today);const end=new Date(today);end.setDate(end.getDate()+6);document.getElementById("end").value=iso(end);}
async function initializeWeekSettings(){const settings=await api("/api/household/settings");const select=document.getElementById("week-start");select.value=settings.financial_week_start||"Friday";alignToWeekStart(select.value);await loadWeeklyCashControl(settings);}

async function loadWeeklyCashControl(existingSettings){
 const settings=existingSettings||await api("/api/household/settings");
 const start=document.getElementById("start").value;
 const weekInput=document.getElementById("weekly-cash-week");
 const amountInput=document.getElementById("weekly-cash-amount");
 const estimate=document.getElementById("weekly-cash-estimate");
 const useEstimate=document.getElementById("weekly-cash-use-estimate");
 weekInput.value=start;
 weekInput.max=start;
 amountInput.value=settings.starting_cash_effective_week===start?(settings.starting_cash_cents/100).toFixed(2):"";
 if(settings.suggested_cash_balance_cents!==null&&settings.suggested_cash_balance_cents!==undefined&&settings.suggested_cash_effective_week===start){
  estimate.textContent="Estimated opening balance for "+start+": "+dollars(settings.suggested_cash_balance_cents)+" based on "+settings.suggested_cash_transaction_count+" recorded transaction(s) since the confirmed snapshot dated "+settings.suggested_cash_based_on_effective_week+". This is not a confirmed balance; compare it with actual cash.";
  useEstimate.hidden=false;
  useEstimate.onclick=()=>{if(weekInput.value!==settings.suggested_cash_effective_week){message("This estimate only applies to the current financial week.","error");return;}amountInput.value=(settings.suggested_cash_balance_cents/100).toFixed(2);message("Estimate copied into the form only. Verify actual cash, then choose Confirm cash balance to save it.");};
 }else{
  estimate.textContent=settings.starting_cash_effective_week===start?"A confirmed snapshot already exists for this week. Transactions recorded during this week remain separate forecast activity.":"No estimate is available for this week. Confirm the actual opening balance if known.";
  useEstimate.hidden=true;
  useEstimate.onclick=null;
 }
 const status=document.getElementById("weekly-cash-status");
 status.textContent=settings.starting_cash_confirmed_at?"Last confirmed "+settings.starting_cash_confirmed_at+" · effective week "+settings.starting_cash_effective_week+(settings.starting_cash_is_stale?" · OUT OF DATE — please reconfirm.":"."):"No confirmed cash snapshot is available yet.";
}

document.getElementById("weekly-cash-form").addEventListener("submit",async event=>{
 event.preventDefault();
 const form=event.currentTarget;
 const button=form.querySelector("button[type=submit]");
 const status=document.getElementById("weekly-cash-status");
 button.disabled=true;
 try{
  const amount=Number(document.getElementById("weekly-cash-amount").value);
  const effectiveWeek=document.getElementById("weekly-cash-week").value;
  if(!Number.isFinite(amount)||amount<0)throw Error("Enter a cash balance of zero or more.");
  const response=await fetch("/api/household/settings",{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({starting_cash_cents:Math.round((amount+Number.EPSILON)*100),starting_cash_effective_week:effectiveWeek})});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw Error(data.detail||"Could not confirm the cash balance.");
  status.textContent="Cash balance confirmed for the week starting "+data.starting_cash_effective_week+".";
  await load();
  await loadWeeklyCashControl();
 }catch(error){status.textContent=error.message;message(error.message,"error");}
 finally{button.disabled=false;}
});
document.getElementById("week-start").addEventListener("change",()=>alignToWeekStart(document.getElementById("week-start").value));
document.getElementById("save-week-start").addEventListener("click",async()=>{try{const value=document.getElementById("week-start").value;const response=await fetch("/api/household/settings",{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({financial_week_start:value})});const data=await response.json().catch(()=>({}));if(!response.ok)throw Error(data.detail||"Could not save week start.");alignToWeekStart(value);message("Financial week start saved as "+value+".");await load();await loadWeeklyCashControl();}catch(e){message(e.message,"error");}});
document.getElementById("allocation-form").addEventListener("submit",async event=>{event.preventDefault();try{const type=document.getElementById("allocation-type").value;const amount=Math.round(Number(document.getElementById("allocation-amount").value)*100);if(!Number.isFinite(amount)||amount<=0)throw Error("Enter a valid positive amount.");const body={allocation_type:type,amount_cents:amount,allocation_date:iso(new Date()),description:document.getElementById("allocation-description").value};if(type==="debt")body.target_debt_id=Number(document.getElementById("allocation-debt").value);if(type==="savings")body.savings_goal_id=Number(document.getElementById("allocation-savings").value);await post("/api/surplus-allocations",body);document.getElementById("allocation-amount").value="";document.getElementById("allocation-description").value="";message("Allocation saved. The remaining safe surplus has been recalculated.");await load();}catch(e){message(e.message,"error");}});
document.getElementById("refresh").addEventListener("click",()=>load().catch(e=>message(e.message,"error")));
initializeWeekSettings().then(load).catch(e=>message(e.message,"error"));
</script></body></html>"""

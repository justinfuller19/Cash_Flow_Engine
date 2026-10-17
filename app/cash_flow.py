"""Weekly household cash-flow forecast from saved schedules and transactions."""
from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from . import database
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


@router.get("/api/cash-flow/weekly")
def weekly_cash_flow(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
):
    """Return a seven-day cash forecast, reconciling explicitly linked transactions."""
    start = start_date or date.today()
    end = end_date or (start + timedelta(days=6))
    if end < start:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    if (end - start).days > 30:
        raise HTTPException(status_code=422, detail="Cash-flow forecast window cannot exceed 31 days")

    with database.connect() as db:
        settings = db.execute(
            "SELECT starting_cash_cents, buffer_target_cents FROM household_settings WHERE id = 1"
        ).fetchone()
        starting_cash = settings["starting_cash_cents"] if settings else 0
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
        used_transactions: set[int] = set()
        expected_income = []
        covered_income = []
        for item in income_occurrences:
            match = _take_matching_transaction(
                all_transactions, used_transactions, "income", item["source_id"],
                date.fromisoformat(item["date"]), 7,
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
            """SELECT id, name, minimum_payment_cents, next_due_date, due_day_of_month
               FROM credit_cards WHERE active = 1 ORDER BY id"""
        ).fetchall()
        for row in rows:
            anchor = _date(row["next_due_date"])
            due_dates = (
                occurrences_between(anchor, "monthly", start, end)
                if anchor else _monthly_due_dates(row["due_day_of_month"], start, end)
            )
            for due in due_dates:
                match = _take_matching_transaction(
                    all_transactions, used_transactions, "debt", row["id"], due, 7,
                )
                debts.append({
                    "id": row["id"], "name": row["name"], "due_date": due.isoformat(),
                    "amount_cents": row["minimum_payment_cents"],
                    "status": "recorded" if match else "due",
                    "covered_by_transaction_id": match["id"] if match else None,
                    "transaction_date": match["transaction_date"] if match else None,
                })
        debts.sort(key=lambda item: (item["due_date"], item["name"]))

        goals = db.execute(
            """SELECT id, name, contribution_per_period_cents, priority
               FROM savings_goals WHERE active = 1 AND contribution_per_period_cents > 0
               ORDER BY priority, name COLLATE NOCASE, id"""
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

    uncovered_bills = [item for item in bills if item["status"] == "due"]
    uncovered_debts = [item for item in debts if item["status"] == "due"]
    expected_income_cents = sum(item["amount_cents"] for item in expected_income)
    bills_due_cents = sum(item["amount_cents"] for item in uncovered_bills)
    minimum_debt_cents = sum(item["amount_cents"] for item in uncovered_debts)

    # A savings contribution is planned once per distinct payday date, not once
    # per household member or income source on the same day.
    payday_dates = {item["date"] for item in income_occurrences}
    planned_savings = sum(row["contribution_per_period_cents"] for row in goals) * len(payday_dates)
    projected_cash = (
        starting_cash + expected_income_cents + transaction_income
        - bills_due_cents - minimum_debt_cents - transaction_expenses
    )
    buffer_replenishment = max(0, buffer_target - projected_cash)
    available_after_buffer = max(0, projected_cash - buffer_target)
    funded_savings = min(planned_savings, available_after_buffer)
    savings_shortfall = planned_savings - funded_savings
    free_to_spend = max(0, available_after_buffer - funded_savings)

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "starting_cash_cents": starting_cash,
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
            item["amount_cents"] for item in debts if item["status"] == "recorded"
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
        "transfers": transaction_transfers,
        "planned_savings_cents": planned_savings,
        "funded_savings_cents": funded_savings,
        "savings_shortfall_cents": savings_shortfall,
        "projected_cash_before_buffer_cents": projected_cash,
        "buffer_replenishment_cents": buffer_replenishment,
        "projected_shortfall_cents": max(0, -projected_cash),
        "buffer_shortfall_cents": max(0, buffer_target - projected_cash),
        "free_to_spend_cents": free_to_spend,
        "extra_debt_payoff_capacity_cents": free_to_spend,
        "notes": [
            "Only active scheduled income, bills, debts, and savings goals are included.",
            "Link a recorded transaction to the scheduled item it paid or received to prevent duplicate counting.",
            "Linked transactions match scheduled occurrences within seven days, or the bill's larger flexible window.",
            "Transfers are listed for reference but do not change household cash totals.",
            "All monetary calculations use integer cents; this report never initiates payments or transfers.",
        ],
    }

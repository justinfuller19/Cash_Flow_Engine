"""Build the rolling pay-period forecast from saved household records."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from fastapi import HTTPException

from .forecast import occurrences_around
from .pay_period_forecast import (
    calculate_pay_periods,
    plan_savings_contributions,
    project_debt_schedule,
    bill_funding_shortfalls_from_forecast,
)
from .pay_periods import group_paychecks, period_for_due_date


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def _financial_week_start(today: date, weekday_name: str) -> date:
    weekday = {
        "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
        "Friday": 4, "Saturday": 5, "Sunday": 6,
    }.get(weekday_name, 4)
    return today - timedelta(days=(today.weekday() - weekday) % 7)


def _as_date(value) -> date | None:
    return date.fromisoformat(value) if value else None


def _money(value) -> int:
    amount = int(value or 0)
    if amount < 0:
        raise ValueError("Stored forecast amounts cannot be negative")
    return amount


def _build_periods(payday_dates: set[date], today: date, horizon_end: date, window_days: int) -> list[dict]:
    groups = group_paychecks(sorted(payday_dates), window_days)
    if not groups:
        return [{
            "period_start": today,
            "period_end": horizon_end,
            "paycheck_dates": (),
            "is_current_period": True,
            "synthetic_current_period": True,
        }]
    current_index = next(
        (i for i, group in enumerate(groups)
         if group["period_start"] <= today
         and (group["period_end"] is None or today <= group["period_end"])),
        None,
    )
    result = []
    if current_index is None:
        first_future = next((g for g in groups if g["period_start"] > today), None)
        if first_future is None:
            # A one-time/irregular schedule may have no future payday. Keep a
            # current planning window rather than inventing future income.
            result.append({
                "period_start": today,
                "period_end": horizon_end,
                "paycheck_dates": (),
                "is_current_period": True,
                "synthetic_current_period": True,
            })
            return result
        result.append({
            "period_start": today,
            "period_end": min(horizon_end, first_future["period_start"] - timedelta(days=1)),
            "paycheck_dates": (),
            "is_current_period": True,
            "synthetic_current_period": True,
        })
        selected_groups = [g for g in groups if today < g["period_start"] <= horizon_end]
    else:
        selected_groups = groups[current_index:]
    for group in selected_groups:
        if group["period_start"] > horizon_end:
            continue
        result.append({
            **group,
            "period_end": min(group["period_end"] or horizon_end, horizon_end),
            "is_current_period": False if current_index is None else group is groups[current_index],
            "synthetic_current_period": False,
        })
    return result


def build_pay_period_forecast(db, today: date | None = None) -> dict:
    """Return the next three calendar months of pay-period projections.

    The current period opens from today's cash snapshot plus transactions since
    that snapshot. Only unpaid bills from today onward and paychecks not yet
    received are projected, preventing historical cash activity from being
    counted twice.
    """
    today = today or date.today()
    horizon_end = _add_months(today, 3)
    settings = db.execute(
        """SELECT starting_cash_cents, financial_week_start,
                  paycheck_group_window_days, end_of_period_target_cents
           FROM household_settings WHERE id = 1"""
    ).fetchone()
    if settings is None:
        raise HTTPException(status_code=500, detail="Household settings are not initialized")

    income_rows = [dict(row) for row in db.execute(
        """SELECT id, person_id, name, net_amount_cents, frequency, next_payday,
                  active, end_date
           FROM income_sources WHERE active = 1 ORDER BY id"""
    ).fetchall()]
    income_overrides: dict[int, list[dict]] = {}
    for row in income_rows:
        income_overrides[row["id"]] = [dict(item) for item in db.execute(
            """SELECT start_date, end_date, amount_cents
               FROM income_amount_overrides WHERE income_source_id = ?
               ORDER BY start_date""", (row["id"],)
        ).fetchall()]

    history_start = today - timedelta(days=120)
    income_occurrences: list[dict] = []
    payday_dates: set[date] = set()
    for source in income_rows:
        anchor = _as_date(source["next_payday"])
        if anchor is None:
            continue
        dates = occurrences_around(
            anchor, source["frequency"], history_start, horizon_end
        )
        end_date = _as_date(source.get("end_date"))
        for payday in dates:
            if end_date and payday > end_date:
                continue
            amount = _money(source["net_amount_cents"])
            for override in income_overrides[source["id"]]:
                if override["start_date"] <= payday.isoformat() <= override["end_date"]:
                    amount = _money(override["amount_cents"])
                    break
            income_occurrences.append({
                "income_source_id": source["id"],
                "name": source["name"],
                "person_id": source["person_id"],
                "date": payday,
                "amount_cents": amount,
            })
            payday_dates.add(payday)

    periods = _build_periods(
        payday_dates, today, horizon_end, settings["paycheck_group_window_days"]
    )
    for index, period in enumerate(periods):
        period["index"] = index
        period["start_date"] = period["period_start"].isoformat()
        period["end_date"] = period["period_end"].isoformat()
        period["income_items"] = []
        period["bill_items"] = []
        period["transaction_items"] = []

    def owning_period(day: date) -> dict | None:
        return next((period for period in periods
                     if period["period_start"] <= day <= period["period_end"]), None)

    # Every paycheck in a close-payday group belongs to the period beginning
    # on that group's last payday, even when the first payday precedes it.
    paycheck_period_by_date = {
        payday: period
        for period in periods
        for payday in period.get("paycheck_dates", ())
    }

    future_transactions = [dict(row) for row in db.execute(
        """SELECT id, transaction_date, transaction_type, linked_obligation_type,
                  linked_obligation_id, category, description, amount_cents,
                  debt_balance_applied
           FROM transactions
           WHERE transaction_date > ? AND transaction_date <= ?
             AND transaction_type IN ('income', 'expense')
           ORDER BY transaction_date, id""",
        (today.isoformat(), horizon_end.isoformat()),
    ).fetchall()]

    def transaction_matches(transaction: dict, link_type: str, link_id: int,
                            scheduled_day: date, window_days: int) -> bool:
        if transaction.get("linked_obligation_type") != link_type:
            return False
        if transaction.get("linked_obligation_id") != link_id:
            return False
        transaction_day = _as_date(transaction["transaction_date"])
        return transaction_day is not None and abs((transaction_day - scheduled_day).days) <= window_days

    income_by_date: dict[date, list[dict]] = {}
    for item in income_occurrences:
        income_by_date.setdefault(item["date"], []).append(item)
    for payday, items in income_by_date.items():
        if payday <= today:
            continue
        period = paycheck_period_by_date.get(payday)
        if period is None:
            continue
        for item in items:
            source_name = item["name"].strip().lower()
            matched = False
            for tx in future_transactions:
                if transaction_matches(tx, "income", item["income_source_id"], payday, 2):
                    matched = True
                    break
                description = (tx.get("description") or "").lower()
                tx_day = _as_date(tx["transaction_date"])
                if (
                    tx.get("linked_obligation_type") is None
                    and abs(int(tx["amount_cents"])) == item["amount_cents"]
                    and tx_day is not None and abs((tx_day - payday).days) <= 2
                    and (
                        source_name in description
                        or ("paycheck" in description and sum(
                            1 for candidate in income_occurrences
                            if candidate["date"] > today
                            and candidate["amount_cents"] == item["amount_cents"]
                            and abs((candidate["date"] - tx_day).days) <= 2
                        ) == 1)
                    )
                ):
                    matched = True
                    break
            if not matched:
                detail = {**item, "date": payday.isoformat()}
                period["income_items"].append(detail)

    # Future recorded income is the authoritative amount for its date and is
    # counted once; matching a scheduled paycheck suppresses its estimate above.
    for transaction in future_transactions:
        day = _as_date(transaction["transaction_date"])
        matched_payday_period = None
        if transaction["transaction_type"] == "income" and day:
            description = (transaction.get("description") or "").lower()
            match = next((
                item for item in income_occurrences
                if item["date"] > today
                and abs((item["date"] - day).days) <= 2
                and (
                    (transaction.get("linked_obligation_type") == "income"
                     and transaction.get("linked_obligation_id") == item["income_source_id"])
                    or (transaction.get("linked_obligation_type") is None
                        and abs(int(transaction["amount_cents"])) == item["amount_cents"]
                        and (
                            item["name"].strip().lower() in description
                            or ("paycheck" in description and sum(
                                1 for candidate in income_occurrences
                                if candidate["date"] > today
                                and candidate["amount_cents"] == item["amount_cents"]
                                and abs((candidate["date"] - day).days) <= 2
                            ) == 1)
                        ))
                )
            ), None)
            if match:
                matched_payday_period = paycheck_period_by_date.get(match["date"])
        period = matched_payday_period or (owning_period(day) if day else None)
        if period is None:
            continue
        amount = abs(int(transaction["amount_cents"]))
        detail = {
            "transaction_id": transaction["id"],
            "date": transaction["transaction_date"],
            "description": transaction["description"],
            "amount_cents": amount,
            "type": transaction["transaction_type"],
        }
        period["transaction_items"].append(detail)

    bill_match_transactions = [dict(row) for row in db.execute(
        """SELECT id, transaction_date, transaction_type, linked_obligation_type,
                  linked_obligation_id, category, description, amount_cents
           FROM transactions
           WHERE transaction_date >= ? AND transaction_date <= ?
             AND transaction_type = 'expense'
           ORDER BY transaction_date, id""",
        ((today - timedelta(days=31)).isoformat(), horizon_end.isoformat()),
    ).fetchall()]

    bills = [dict(row) for row in db.execute(
        """SELECT id, name, amount_cents, due_day_of_month, next_due_date,
                  flexible_window_days, category, frequency, autopay, active
           FROM bills WHERE active = 1 ORDER BY id"""
    ).fetchall()]
    for bill in bills:
        anchor = _as_date(bill.get("next_due_date"))
        if anchor is None and bill.get("due_day_of_month"):
            due_day = min(int(bill["due_day_of_month"]), monthrange(today.year, today.month)[1])
            anchor = date(today.year, today.month, due_day)
        if anchor is None:
            continue
        for due_date in occurrences_around(anchor, bill["frequency"], today, horizon_end):
            period = owning_period(due_date)
            if period is None:
                continue
            flexible_days = int(bill.get("flexible_window_days") or 0)
            paid = any(
                tx["transaction_type"] == "expense"
                and transaction_matches(tx, "bill", bill["id"], due_date, flexible_days)
                for tx in bill_match_transactions
            )
            if paid:
                continue
            item = {
                "bill_id": bill["id"],
                "name": bill["name"],
                "category": bill["category"],
                "due_date": due_date.isoformat(),
                "amount_cents": _money(bill["amount_cents"]),
                "autopay": bool(bill.get("autopay")),
            }
            period["bill_items"].append(item)

    debt_rows = [dict(row) for row in db.execute(
        """SELECT id, name, balance_cents, minimum_payment_cents,
                  planned_payment_cents, apr_percent, next_due_date, interest_date, active
           FROM credit_cards WHERE active = 1 ORDER BY id"""
    ).fetchall()]
    actual_debt_payments = [
        {
            "id": tx["id"],
            "debt_id": tx["linked_obligation_id"],
            "date": tx["transaction_date"],
            "amount_cents": abs(int(tx["amount_cents"])),
        }
        for tx in future_transactions
        if tx["transaction_type"] == "expense"
        and tx.get("linked_obligation_type") == "debt"
        and tx.get("linked_obligation_id") is not None
        and not tx.get("debt_balance_applied", 0)
    ]
    recently_applied_debt_payments = [dict(row) for row in db.execute(
        """SELECT id, linked_obligation_id, transaction_date
           FROM transactions
           WHERE transaction_type = 'expense' AND linked_obligation_type = 'debt'
             AND debt_balance_applied = 1 AND transaction_date BETWEEN ? AND ?""",
        ((today - timedelta(days=7)).isoformat(), today.isoformat()),
    ).fetchall()]
    for debt in debt_rows:
        due_date = _as_date(debt.get("next_due_date"))
        if due_date is None:
            continue
        if any(
            payment["linked_obligation_id"] == debt["id"]
            and abs((_as_date(payment["transaction_date"]) - due_date).days) <= 7
            for payment in recently_applied_debt_payments
        ):
            debt["next_due_date"] = _add_months(due_date, 1).isoformat()
    debt_periods = project_debt_schedule(
        debt_rows,
        [{"start_date": p["start_date"], "end_date": p["end_date"]} for p in periods],
        today.isoformat(),
        actual_payments=actual_debt_payments,
    )
    debt_period_by_start = {p["start_date"]: p for p in debt_periods["periods"]}

    goals = [dict(row) for row in db.execute(
        """SELECT id, name, target_amount_cents, current_balance_cents,
                  target_date, contribution_per_period_cents, last_contribution_date, active
           FROM savings_goals WHERE active = 1 ORDER BY priority, id"""
    ).fetchall()]
    current_calendar_period = next((p for p in periods if p["is_current_period"]), periods[0])
    completed_savings_periods = set()
    for goal in goals:
        last_contribution = _as_date(goal.get("last_contribution_date"))
        if (
            last_contribution is not None
            and current_calendar_period["period_start"] <= last_contribution <= today
        ):
            completed_savings_periods.add((goal["id"], current_calendar_period["start_date"]))

    savings = plan_savings_contributions(
        [{
            "id": goal["id"],
            "name": goal["name"],
            "target_amount_cents": _money(goal["target_amount_cents"]),
            "current_balance_cents": _money(goal["current_balance_cents"]),
            "target_date": goal["target_date"],
            "ongoing_per_period_cents": _money(goal["contribution_per_period_cents"]),
        } for goal in goals],
        [p["start_date"] for p in periods],
        completed_periods=completed_savings_periods,
    )
    savings_by_period: dict[str, list[dict]] = {}
    for goal in savings:
        for item in goal["periods"]:
            if item["planned_contribution_cents"]:
                savings_by_period.setdefault(item["period_start"], []).append({
                    "goal_id": goal["goal_id"],
                    "name": goal["name"],
                    "amount_cents": item["planned_contribution_cents"],
                })

    # The saved cash snapshot is the opening balance at its effective date.
    # Signed, non-transfer ledger activity after that date is applied once.
    snapshot = db.execute(
        """SELECT effective_week_start, balance_cents
           FROM cash_balance_snapshots
           WHERE effective_week_start <= ?
           ORDER BY effective_week_start DESC, id DESC LIMIT 1""",
        (today.isoformat(),),
    ).fetchone()
    if snapshot:
        snapshot_date = _as_date(snapshot["effective_week_start"])
        starting_cash = _money(snapshot["balance_cents"])
    else:
        snapshot_date = _financial_week_start(today, settings["financial_week_start"])
        starting_cash = _money(settings["starting_cash_cents"])
    activity = db.execute(
        """SELECT COALESCE(SUM(amount_cents), 0)
           FROM transactions
           WHERE transaction_date >= ? AND transaction_date <= ?
             AND transaction_type IN ('income', 'expense')""",
        (snapshot_date.isoformat(), today.isoformat()),
    ).fetchone()[0]
    starting_cash = starting_cash + int(activity or 0)

    # Current-period income is already included in the cash snapshot. Only
    # future recorded income/expenses are included in period projections.
    projection_input = []
    for period in periods:
        debt_period = debt_period_by_start.get(period["start_date"], {})
        savings_items = savings_by_period.get(period["start_date"], [])
        income_items = period["income_items"]
        future_tx_income = sum(
            item["amount_cents"] for item in period["transaction_items"] if item["type"] == "income"
        )
        future_tx_expense = sum(
            item["amount_cents"] for item in period["transaction_items"] if item["type"] == "expense"

        )
        projection_input.append({
            "start_date": period["start_date"],
            "end_date": period["end_date"],
            "starting_cash_cents": starting_cash if period["is_current_period"] else 0,
            "income_cents": sum(item["amount_cents"] for item in income_items) + future_tx_income,
            "bills_cents": sum(item["amount_cents"] for item in period["bill_items"]),
            "savings_cents": sum(item["amount_cents"] for item in savings_items),
            "debt_payments_cents": debt_period.get("debt_payments_cents", 0),
            "other_expenses_cents": future_tx_expense,
        })
        period["savings_items"] = savings_items
        period["debt_events"] = debt_period.get("debt_events", [])
        period["estimated_interest_cents"] = debt_period.get("estimated_interest_cents", 0)

    if projection_input:
        # A synthetic current period can end before the first payday; every
        # following period starts with the previous projected ending cash.
        projection = calculate_pay_periods(
            projection_input, int(settings["end_of_period_target_cents"])
        )
    else:
        projection = calculate_pay_periods([], int(settings["end_of_period_target_cents"]))

    response_periods = []
    for calendar_period, projection_period in zip(periods, projection["periods"]):
        response_periods.append({
            **projection_period,
            "paycheck_dates": [day.isoformat() for day in calendar_period.get("paycheck_dates", ())],
            "income_items": calendar_period["income_items"],
            "bills": calendar_period["bill_items"],
            "savings": calendar_period["savings_items"],
            "debt_events": calendar_period["debt_events"],
            "estimated_interest_cents": calendar_period["estimated_interest_cents"],
            "transactions": calendar_period["transaction_items"],
            "is_current_period": calendar_period["is_current_period"],
        })

    result = {
        "generated_on": today.isoformat(),
        "horizon_end": horizon_end.isoformat(),
        "current_cash_cents": starting_cash,
        "end_of_period_target_cents": projection["end_of_period_target_cents"],
        "reserved_cash_cents": projection["reserved_cash_cents"],
        "max_spendable_cents": projection["max_spendable_cents"],
        "spendable_while_keeping_target_cents": projection["spendable_while_keeping_target_cents"],
        "target_maintainable": projection["target_maintainable"],
        "warnings": list(projection["warnings"]),
        "periods": response_periods,
        "projected_debts": debt_periods.get("debts", []),
        "savings_goals": savings,
    }
    bill_shortfalls = bill_funding_shortfalls_from_forecast(result)
    result["bill_shortfalls"] = bill_shortfalls
    result["warnings"].extend({
        "type": "bill_underfunded",
        "start_date": item["due_date"],
        "name": item["name"],
        "shortfall_cents": item["shortfall_cents"],
    } for item in bill_shortfalls)
    return result

def post_due_scheduled_transactions(db, today: date | None = None) -> dict:
    """Idempotently post recent due paychecks and bills to the internal ledger.

    This never contacts a bank or initiates a payment. Entries are ordinary,
    linked transactions that users can edit or delete to correct the forecast.
    A seven-day catch-up window handles days when the app was not opened.
    """
    from .forecast import occurrences_between

    today = today or date.today()
    catch_up_start = today - timedelta(days=7)
    created_income = []
    created_bills = []

    # Future-dated linked debt payments do not reduce the saved balance until
    # their transaction date arrives. Apply them once when the app is next used.
    due_debt_payments = db.execute(
        """SELECT id, linked_obligation_id, amount_cents
           FROM transactions
           WHERE transaction_type = 'expense' AND linked_obligation_type = 'debt'
             AND transaction_date <= ? AND debt_balance_applied = 0""",
        (today.isoformat(),),
    ).fetchall()
    for payment in due_debt_payments:
        db.execute(
            "UPDATE credit_cards SET balance_cents = MAX(0, balance_cents - ?) WHERE id = ?",
            (abs(int(payment["amount_cents"])), payment["linked_obligation_id"]),
        )
        db.execute(
            "UPDATE transactions SET debt_balance_applied = 1 WHERE id = ?",
            (payment["id"],),
        )

    income_rows = [dict(row) for row in db.execute(
        """SELECT id, name, net_amount_cents, frequency, next_payday, end_date
           FROM income_sources WHERE active = 1 ORDER BY id"""
    ).fetchall()]
    for source in income_rows:
        anchor = _as_date(source.get("next_payday"))
        if anchor is None or anchor > today:
            continue
        end_date = _as_date(source.get("end_date"))
        window_start = max(anchor, catch_up_start)
        dates = occurrences_between(anchor, source["frequency"], window_start, today)
        overrides = [dict(row) for row in db.execute(
            """SELECT start_date, end_date, amount_cents
               FROM income_amount_overrides WHERE income_source_id = ?
               ORDER BY start_date""", (source["id"],)
        ).fetchall()]
        for payday in dates:
            if end_date and payday > end_date:
                continue
            existing_income = db.execute(
                """SELECT id, description, amount_cents, linked_obligation_type,
                          linked_obligation_id
                   FROM transactions
                   WHERE transaction_type = 'income'
                     AND transaction_date BETWEEN ? AND ?
                   ORDER BY id""",
                ((payday - timedelta(days=2)).isoformat(),
                 (payday + timedelta(days=2)).isoformat()),
            ).fetchall()
            amount = _money(source["net_amount_cents"])
            for override in overrides:
                if override["start_date"] <= payday.isoformat() <= override["end_date"]:
                    amount = _money(override["amount_cents"])
                    break
            source_name = source["name"].strip().lower()
            if any(
                (row["linked_obligation_type"] == "income"
                 and row["linked_obligation_id"] == source["id"])
                or (row["linked_obligation_type"] is None
                    and abs(int(row["amount_cents"])) == amount
                    and ("paycheck" in (row["description"] or "").lower()
                         or source_name in (row["description"] or "").lower()))
                for row in existing_income
            ):
                continue
            cursor = db.execute(
                """INSERT INTO transactions
                   (transaction_date, category, transaction_type, linked_obligation_type,
                    linked_obligation_id, description, amount_cents, notes)
                   VALUES (?, 'paychecks', 'income', 'income', ?, ?, ?,
                           'Automatically posted to the internal ledger; edit or delete to correct.')""",
                (payday.isoformat(), source["id"], f"Paycheck received — {source['name']}", amount),
            )
            created_income.append(cursor.lastrowid)

    bill_rows = [dict(row) for row in db.execute(
        """SELECT id, name, amount_cents, due_day_of_month, next_due_date,
                  flexible_window_days, category, frequency
           FROM bills WHERE active = 1 ORDER BY id"""
    ).fetchall()]
    for bill in bill_rows:
        anchor = _as_date(bill.get("next_due_date"))
        if anchor is None and bill.get("due_day_of_month"):
            due_day = min(int(bill["due_day_of_month"]), monthrange(today.year, today.month)[1])
            anchor = date(today.year, today.month, due_day)
        if anchor is None or anchor > today:
            continue
        window_start = max(anchor, catch_up_start)
        dates = occurrences_between(anchor, bill["frequency"], window_start, today)
        used_transactions: set[int] = set()
        for due_date in dates:
            flex = int(bill.get("flexible_window_days") or 0)
            pending_match = db.execute(
                """SELECT id FROM bill_payment_match_suggestions
                   WHERE bill_id = ? AND due_date = ? AND status = 'pending' LIMIT 1""",
                (bill["id"], due_date.isoformat()),
            ).fetchone()
            if pending_match:
                continue
            candidates = db.execute(
                """SELECT id, transaction_date, description, amount_cents, category,
                          linked_obligation_type, linked_obligation_id
                   FROM transactions
                   WHERE transaction_type = 'expense'
                     AND transaction_date BETWEEN ? AND ?
                   ORDER BY ABS(julianday(transaction_date) - julianday(?)), id""",
                ((due_date - timedelta(days=flex)).isoformat(),
                 (due_date + timedelta(days=flex)).isoformat(), due_date.isoformat()),
            ).fetchall()
            bill_name = bill["name"].strip().lower()
            existing = next((row for row in candidates if row["id"] not in used_transactions and (
                (row["linked_obligation_type"] == "bill" and row["linked_obligation_id"] == bill["id"])
                or (row["linked_obligation_type"] is None
                    and abs(int(row["amount_cents"])) == _money(bill["amount_cents"])
                    and ((row["category"] or "").lower() == (bill.get("category") or "").lower()
                         or bill_name in (row["description"] or "").lower()))
            )), None)
            if existing:
                used_transactions.add(existing["id"])
                continue
            cursor = db.execute(
                """INSERT INTO transactions
                   (transaction_date, category, transaction_type, linked_obligation_type,
                    linked_obligation_id, description, amount_cents, notes)
                   VALUES (?, ?, 'expense', 'bill', ?, ?, ?, ?)""",
                (due_date.isoformat(), bill.get("category") or "other", bill["id"],
                 f"Bill paid — {bill['name']}", -_money(bill["amount_cents"]),
                 "Automatically posted to the internal ledger; edit or delete to correct."),
            )
            created_bills.append(cursor.lastrowid)

    return {
        "posted_paychecks": len(created_income),
        "posted_bills": len(created_bills),
        "transaction_ids": created_income + created_bills,
    }

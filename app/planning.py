"""Persisted payday planning from the saved household setup."""
import sqlite3
from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse

from . import database
from .engine import (
    Bill, BufferReservation, CreditCard, Income, PaydayRequest, SavingsGoal,
    generate_payday_plan,
)
from .forecast import occurrences_between
from .planning_schemas import PlanGenerateRequest, SavedPlanOut

router = APIRouter()


def _date(value) -> date | None:
    return date.fromisoformat(value) if value else None


def _next_day_of_month(day: int, start: date, end: date) -> date:
    """Find the next due-day occurrence on/after start, even if it is beyond end."""
    candidate = date(start.year, start.month, min(day, monthrange(start.year, start.month)[1]))
    if candidate < start:
        month_index = start.year * 12 + start.month
        year, month0 = divmod(month_index, 12)
        month = month0 + 1
        candidate = date(year, month, min(day, monthrange(year, month)[1]))
    return candidate


def _debt_due_date(row, period_start: date, period_end: date) -> date:
    anchor = _date(row["next_due_date"])
    if anchor:
        in_window = occurrences_between(anchor, "monthly", period_start, period_end)
        if in_window:
            return in_window[0]
        after_end = period_end + timedelta(days=1)
        next_dates = occurrences_between(anchor, "monthly", after_end, after_end + timedelta(days=35))
        return next_dates[0] if next_dates else after_end
    if row["due_day_of_month"]:
        return _next_day_of_month(row["due_day_of_month"], period_start, period_end)
    return period_end + timedelta(days=1)


def _load_request(db, body: PlanGenerateRequest) -> PaydayRequest:
    incomes: list[Income] = []
    rows = db.execute(
        """SELECT i.name, i.net_amount_cents, i.frequency, i.next_payday, p.name AS person_name
           FROM income_sources i JOIN persons p ON p.id = i.person_id
           WHERE i.active = 1 AND p.active = 1
           ORDER BY i.next_payday, i.id"""
    ).fetchall()
    for row in rows:
        anchor = date.fromisoformat(row["next_payday"])
        for payday in occurrences_between(anchor, row["frequency"], body.period_start, body.period_end):
            incomes.append(Income(
                name=f'{row["person_name"]} — {row["name"]}',
                amount_cents=row["net_amount_cents"],
                received_date=payday,
            ))

    settings = db.execute(
        "SELECT buffer_target_cents FROM household_settings WHERE id = 1"
    ).fetchone()
    buffer_target = settings["buffer_target_cents"] if settings else 0
    buffers = (
        [BufferReservation(name="Household cash buffer", amount_cents=buffer_target)]
        if buffer_target > 0
        else []
    )

    bills: list[Bill] = []
    rows = db.execute(
        """SELECT name, amount_cents, next_due_date, frequency
           FROM bills WHERE active = 1 ORDER BY next_due_date, id"""
    ).fetchall()
    for row in rows:
        anchor = _date(row["next_due_date"])
        if anchor is None:
            continue
        for due_date in occurrences_between(anchor, row["frequency"], body.period_start, body.period_end):
            bills.append(Bill(name=row["name"], amount_cents=row["amount_cents"], due_date=due_date))

    cards: list[CreditCard] = []
    rows = db.execute(
        """SELECT name, balance_cents, minimum_payment_cents, apr_percent,
                  due_day_of_month, next_due_date
           FROM credit_cards WHERE active = 1 ORDER BY id"""
    ).fetchall()
    for row in rows:
        cards.append(CreditCard(
            name=row["name"],
            balance_cents=row["balance_cents"],
            minimum_payment_cents=row["minimum_payment_cents"],
            due_date=_debt_due_date(row, body.period_start, body.period_end),
            apr_percent=row["apr_percent"],
            active=True,
        ))

    goals = [
        SavingsGoal(
            name=row["name"],
            target_amount_cents=row["target_amount_cents"],
            current_balance_cents=row["current_balance_cents"],
            remaining_periods=1,
            priority=row["priority"],
            contribution_per_period_cents=row["weekly_target_cents"],
        )
        for row in db.execute(
            """SELECT name, target_amount_cents, current_balance_cents, priority,
                      weekly_target_cents
               FROM savings_goals WHERE active = 1 AND weekly_target_cents > 0
               ORDER BY priority, name COLLATE NOCASE, id"""
        ).fetchall()
    ]

    return PaydayRequest(
        plan_date=body.plan_date,
        period_start=body.period_start,
        period_end=body.period_end,
        starting_cash_cents=body.starting_cash_cents,
        incomes=incomes,
        buffers=buffers,
        bills=bills,
        credit_cards=cards,
        savings_goals=goals,
        debt_strategy=body.debt_strategy,
    )


@router.post("/api/plans/generate", response_model=SavedPlanOut, status_code=status.HTTP_201_CREATED)
def generate_saved_plan(body: PlanGenerateRequest):
    try:
        with database.connect() as db:
            db.execute("BEGIN")
            request = _load_request(db, body)
            plan = generate_payday_plan(request)
            cursor = db.execute(
                """INSERT INTO payday_plans
                   (plan_date, period_start, period_end, starting_cash_cents, debt_strategy,
                    income_cents, total_available_cents, allocated_cents, unallocated_cents,
                    unfunded_cents, target_card, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')""",
                (plan.plan_date.isoformat(), plan.period_start.isoformat(), plan.period_end.isoformat(),
                 plan.starting_cash_cents, plan.debt_strategy, plan.income_cents,
                 plan.total_available_cents, plan.allocated_cents, plan.unallocated_cents,
                 plan.unfunded_cents, plan.target_card),
            )
            plan_id = cursor.lastrowid
            for item in plan.items:
                db.execute(
                    """INSERT INTO plan_items
                       (plan_id, category, name, requested_cents, funded_cents, unfunded_cents, due_date, note)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (plan_id, item.category, item.name, item.requested_cents, item.funded_cents,
                     item.unfunded_cents, item.due_date.isoformat() if item.due_date else None, item.note),
                )
            row = db.execute("SELECT * FROM payday_plans WHERE id = ?", (plan_id,)).fetchone()
            return _plan_output(db, row)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Generated plan violates a database constraint") from exc


def _plan_output(db, row) -> dict:
    plan = dict(row)
    items = db.execute(
        """SELECT id, category, name, requested_cents, funded_cents, unfunded_cents,
                  due_date, note, status, actual_amount_cents, actual_date, is_completed
           FROM plan_items WHERE plan_id = ? ORDER BY id""",
        (plan["id"],),
    ).fetchall()
    plan["items"] = [dict(item) | {"is_completed": bool(item["is_completed"])} for item in items]
    plan["is_fully_funded"] = plan["unfunded_cents"] == 0
    return plan


@router.get("/api/plans", response_model=list[SavedPlanOut])
def list_saved_plans():
    with database.connect() as db:
        rows = db.execute("SELECT * FROM payday_plans ORDER BY plan_date DESC, id DESC").fetchall()
        return [_plan_output(db, row) for row in rows]


@router.get("/api/plans/{plan_id}", response_model=SavedPlanOut)
def get_saved_plan(plan_id: int):
    with database.connect() as db:
        row = db.execute("SELECT * FROM payday_plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Payday plan not found")
        return _plan_output(db, row)


@router.delete("/api/plans/{plan_id}")
def delete_saved_plan(plan_id: int):
    with database.connect() as db:
        cursor = db.execute("DELETE FROM payday_plans WHERE id = ?", (plan_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Saved plan not found")
    return {"deleted": True, "id": plan_id}


@router.get("/planner")
def planner_page():
    return RedirectResponse(url="/checklist", status_code=307)


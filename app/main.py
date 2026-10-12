"""CashFlow Engine FastAPI entry point."""
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse

from . import database
from .engine import (
    Bill, BufferReservation, CreditCard, Income, PaydayRequest, SavingsGoal,
    generate_payday_plan,
)
from .household_schemas import (
    IncomeSourceCreate, IncomeSourceOut, PersonCreate, PersonOut,
)
from .schemas import PaydayPlanOut, PaydayPreviewRequest


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database.initialize_database()
    yield


app = FastAPI(
    title="CashFlow Engine",
    description="Self-hosted household cash-flow planning with SQLite-backed household setup.",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {"status": "ok", "app": "CashFlow Engine"}


@app.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CashFlow Engine</title><style>
:root{font-family:system-ui,sans-serif;color:#18212f;background:#f3f6fa}body{max-width:850px;margin:8vh auto;padding:0 20px}
main{background:white;padding:32px;border-radius:18px;box-shadow:0 8px 32px #172b4d12}h1{margin:0 0 8px;color:#155e75}
code{background:#eef2f7;padding:3px 6px;border-radius:5px}.pill{display:inline-block;background:#d1fae5;color:#065f46;padding:5px 9px;border-radius:999px;font-size:13px}
li{margin:8px 0}.muted{color:#5b6678}</style></head><body><main>
<span class="pill">Milestone 2 · SQLite foundation</span><h1>CashFlow Engine</h1>
<p class="muted">A private, self-hosted household cash-flow planner.</p>
<h2>What's working</h2><ul>
<li>Payday allocation engine using integer cents.</li>
<li>Buffers first, then bills, card minimums, savings goals, and extra debt payoff.</li>
<li>Explicit shortfalls plus avalanche and snowball strategies.</li>
<li>SQLite schema for household members, income, bills, cards, savings, transactions, and payday plans.</li>
<li>Persistent household-member and income-source setup APIs.</li></ul>
<p>Use <code>/docs</code> to explore the API. Add household members at <code>POST /api/people</code> and income sources at <code>POST /api/income-sources</code>.</p>
<p class="muted">The management APIs are an early foundation, not a finished finance UI. Keep this service on a trusted network; authentication and deployment hardening are not included.</p>
</main></body></html>"""


@app.get("/api/people", response_model=list[PersonOut])
def list_people():
    with database.connect() as db:
        rows = db.execute(
            "SELECT id, name, default_buffer_cents, created_at FROM persons ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/people", response_model=PersonOut, status_code=status.HTTP_201_CREATED)
def create_person(body: PersonCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name cannot be blank")
    with database.connect() as db:
        cursor = db.execute(
            "INSERT INTO persons (name, default_buffer_cents) VALUES (?, ?)",
            (name, body.default_buffer_cents),
        )
        row = db.execute(
            "SELECT id, name, default_buffer_cents, created_at FROM persons WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return dict(row)


@app.get("/api/income-sources", response_model=list[IncomeSourceOut])
def list_income_sources():
    with database.connect() as db:
        rows = db.execute(
            """SELECT id, person_id, name, net_amount_cents, frequency,
                      next_payday, active, created_at
               FROM income_sources ORDER BY next_payday, id"""
        ).fetchall()
    return [dict(row) | {"active": bool(row["active"])} for row in rows]


@app.post(
    "/api/income-sources",
    response_model=IncomeSourceOut,
    status_code=status.HTTP_201_CREATED,
)
def create_income_source(body: IncomeSourceCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name cannot be blank")
    try:
        with database.connect() as db:
            person = db.execute(
                "SELECT id FROM persons WHERE id = ?", (body.person_id,)
            ).fetchone()
            if person is None:
                raise HTTPException(status_code=404, detail="Household member not found")
            cursor = db.execute(
                """INSERT INTO income_sources
                   (person_id, name, net_amount_cents, frequency, next_payday, active)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    body.person_id, name, body.net_amount_cents, body.frequency,
                    body.next_payday.isoformat(), int(body.active),
                ),
            )
            row = db.execute(
                """SELECT id, person_id, name, net_amount_cents, frequency,
                          next_payday, active, created_at
                   FROM income_sources WHERE id = ?""",
                (cursor.lastrowid,),
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Income source violates a database constraint") from exc
    return dict(row) | {"active": bool(row["active"])}


@app.post("/api/plan/preview", response_model=PaydayPlanOut)
def preview_plan(body: PaydayPreviewRequest):
    try:
        request = PaydayRequest(
            plan_date=body.plan_date,
            period_start=body.period_start,
            period_end=body.period_end,
            starting_cash_cents=body.starting_cash_cents,
            incomes=[Income(**item.model_dump()) for item in body.incomes],
            buffers=[BufferReservation(**item.model_dump()) for item in body.buffers],
            bills=[Bill(**item.model_dump()) for item in body.bills],
            credit_cards=[CreditCard(**item.model_dump()) for item in body.credit_cards],
            savings_goals=[SavingsGoal(**item.model_dump()) for item in body.savings_goals],
            debt_strategy=body.debt_strategy,
        )
        plan = generate_payday_plan(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return PaydayPlanOut(
        plan_date=plan.plan_date,
        period_start=plan.period_start,
        period_end=plan.period_end,
        starting_cash_cents=plan.starting_cash_cents,
        income_cents=plan.income_cents,
        total_available_cents=plan.total_available_cents,
        items=[item.__dict__ for item in plan.items],
        allocated_cents=plan.allocated_cents,
        unallocated_cents=plan.unallocated_cents,
        unfunded_cents=plan.unfunded_cents,
        target_card=plan.target_card,
        debt_strategy=plan.debt_strategy,
        is_fully_funded=plan.is_fully_funded,
    )

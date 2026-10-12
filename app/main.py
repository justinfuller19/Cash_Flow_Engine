"""CashFlow Engine FastAPI entry point (initial MVP)."""
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .engine import (
    Bill, BufferReservation, CreditCard, Income, PaydayRequest, SavingsGoal,
    generate_payday_plan,
)
from .schemas import PaydayPlanOut, PaydayPreviewRequest

app = FastAPI(
    title="CashFlow Engine",
    description="Self-hosted household cash-flow planning. Preview endpoint only; persistence is next.",
    version="0.1.0",
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
<span class="pill">MVP · Calculation engine online</span><h1>CashFlow Engine</h1>
<p class="muted">A private, self-hosted household cash-flow planner.</p>
<h2>What's working in this milestone</h2><ul>
<li>Pure Python payday allocation engine using integer cents.</li>
<li>Buffers first, then bills, card minimums, savings goals, and extra debt payoff.</li>
<li>Explicit shortfalls when there isn't enough cash to fund an item.</li>
<li>Avalanche and snowball target selection.</li>
<li>Automated tests for allocation order and arithmetic.</li></ul>
<p>Use <code>/docs</code> to try the interactive API, especially <code>POST /api/plan/preview</code>.</p>
<p class="muted">This initial milestone does not yet save financial data and should not be exposed to the public internet.</p>
</main></body></html>"""


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

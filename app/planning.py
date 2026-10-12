"""Persisted payday planning from the saved household setup."""
import sqlite3
from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse

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

    buffers = [
        BufferReservation(name=f'{row["name"]} cash buffer', amount_cents=row["default_buffer_cents"])
        for row in db.execute(
            "SELECT name, default_buffer_cents FROM persons WHERE active = 1 AND default_buffer_cents > 0 ORDER BY id"
        ).fetchall()
    ]

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
            contribution_per_period_cents=row["contribution_per_period_cents"],
        )
        for row in db.execute(
            """SELECT name, target_amount_cents, current_balance_cents, priority,
                      contribution_per_period_cents
               FROM savings_goals WHERE active = 1 AND contribution_per_period_cents > 0
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
                  due_date, note, is_completed
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


@router.get("/planner", response_class=HTMLResponse)
def planner_page():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Payday Planner · CashFlow Engine</title>
<style>
:root{font-family:system-ui,sans-serif;color:#172033;background:#f3f6fa}*{box-sizing:border-box}
body{margin:0;padding:24px 14px 48px}main{max-width:1100px;margin:auto}a{color:#0e7490;font-weight:650;text-decoration:none}
h1{color:#155e75;margin:8px 0}.muted{color:#5c687a;line-height:1.5}
.card{background:white;padding:22px;border-radius:16px;box-shadow:0 8px 28px #172b4d0d;margin-top:18px}
form{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:12px;align-items:end}
label{display:block;font-weight:650;font-size:.9rem;margin-bottom:5px}
input,select{width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font:inherit;background:white}
button{border:0;border-radius:8px;padding:10px 12px;background:#0e7490;color:white;font-weight:700;cursor:pointer}
button:disabled{opacity:.55}.status{padding:12px;border-radius:9px;background:#e0f2fe;color:#075985}.error{background:#fee2e2;color:#991b1b}.status[hidden]{display:none}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px}
.metric{background:#f8fafc;border-radius:10px;padding:12px}.metric strong{display:block;font-size:1.15rem;color:#155e75}
.list{padding:0;list-style:none}.list li{padding:12px 0;border-top:1px solid #e2e8f0;overflow-wrap:anywhere}
.title{font-weight:700}.detail{color:#5c687a;font-size:.9rem;margin-top:4px}.shortfall{color:#b91c1c}.ok{color:#166534}
</style></head><body><main>
<a href="/">← Dashboard</a><h1>Payday Planner</h1>
<p class="muted">Generate and save a plan using your active household income, buffers, recurring bills, debts, and savings contributions. This forecasts allocations only; it does not pay bills or move money.</p>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<section class="card"><h2>Build a payday plan</h2>
<form id="plan-form">
<div><label for="plan-date">Plan date</label><input id="plan-date" name="plan_date" type="date" required></div>
<div><label for="period-start">Pay period starts</label><input id="period-start" name="period_start" type="date" required></div>
<div><label for="period-end">Pay period ends</label><input id="period-end" name="period_end" type="date" required></div>
<div><label for="starting-cash">Starting cash available ($)</label><input id="starting-cash" name="starting_cash" type="number" min="0" step="0.01" value="0" required></div>
<div><label for="strategy">Extra debt payoff</label><select id="strategy" name="debt_strategy"><option value="avalanche">Avalanche (highest APR)</option><option value="snowball">Snowball (smallest balance)</option></select></div>
<div><button type="submit">Generate & save plan</button></div>
</form></section>
<section class="card" id="current-plan" hidden><h2>Plan results</h2><div id="summary" class="summary"></div><ul id="items" class="list"></ul></section>
<section class="card"><h2>Saved plan history</h2><ul id="history" class="list"><li>Loading plans…</li></ul></section>
</main>
<script>
const box=document.getElementById("status");
function message(text,error=false){box.textContent=text;box.classList.toggle("error",error);box.hidden=false;}
function dollars(value){return new Intl.NumberFormat(undefined,{style:"currency",currency:"USD"}).format(value/100);}
function cents(value){const n=Number(value);if(!Number.isFinite(n)||n<0)throw Error("Starting cash must be a non-negative amount.");return Math.round((n+Number.EPSILON)*100);}
async function api(url,options={}){const r=await fetch(url,{headers:{"Content-Type":"application/json"},...options});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"Please check the dates and amounts.");return d;}
function el(tag,cls,txt){const n=document.createElement(tag);if(cls)n.className=cls;n.textContent=txt;return n;}
function renderPlan(p){const section=document.getElementById("current-plan");section.hidden=false;const summary=document.getElementById("summary");summary.replaceChildren();
for(const [label,value] of [["Income forecast",dollars(p.income_cents)],["Total available",dollars(p.total_available_cents)],["Allocated",dollars(p.allocated_cents)],["Remaining cash",dollars(p.unallocated_cents)],["Unfunded needs",dollars(p.unfunded_cents)]]){const card=el("div","metric","");card.appendChild(el("span","detail",label));card.appendChild(el("strong","",value));summary.appendChild(card);}
const items=document.getElementById("items");items.replaceChildren();if(!p.items.length)items.appendChild(el("li","muted","No allocations were required in this window."));
for(const item of p.items){const li=el("li");li.appendChild(el("div","title",item.name+" · "+dollars(item.funded_cents)+" / "+dollars(item.requested_cents)));li.appendChild(el("div","detail",item.category+(item.due_date?" · due "+item.due_date:"")+(item.note?" · "+item.note:"")));if(item.unfunded_cents)li.appendChild(el("div","detail shortfall","Short by "+dollars(item.unfunded_cents)));items.appendChild(li);}
}
async function refreshHistory(){const plans=await api("/api/plans");const list=document.getElementById("history");list.replaceChildren();if(!plans.length){list.appendChild(el("li","muted","No saved plans yet."));return;}
for(const p of plans){const li=el("li");li.appendChild(el("div","title","Plan "+p.id+" · "+p.period_start+" to "+p.period_end));li.appendChild(el("div","detail","Available "+dollars(p.total_available_cents)+" · allocated "+dollars(p.allocated_cents)+(p.unfunded_cents?" · short "+dollars(p.unfunded_cents):" · fully funded")));const b=el("button","","View plan");b.type="button";b.addEventListener("click",async()=>{try{renderPlan(await api("/api/plans/"+p.id));}catch(e){message(e.message,true);}});li.appendChild(b);list.appendChild(li);}}
document.getElementById("plan-form").addEventListener("submit",async(e)=>{e.preventDefault();const f=e.currentTarget;const b=f.querySelector("button");b.disabled=true;try{const p=await api("/api/plans/generate",{method:"POST",body:JSON.stringify({plan_date:f.elements.plan_date.value,period_start:f.elements.period_start.value,period_end:f.elements.period_end.value,starting_cash_cents:cents(f.elements.starting_cash.value),debt_strategy:f.elements.debt_strategy.value})});renderPlan(p);await refreshHistory();message("Payday plan generated and saved.");}catch(err){message(err.message,true);}finally{b.disabled=false;}});
const today=new Date();const iso=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,"0"),String(d.getDate()).padStart(2,"0")].join("-");
document.getElementById("plan-date").value=iso(today);document.getElementById("period-start").value=iso(today);const end=new Date(today);end.setDate(end.getDate()+13);document.getElementById("period-end").value=iso(end);
refreshHistory().catch(e=>message("Could not load plan history: "+e.message,true));
</script></body></html>"""

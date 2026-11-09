"""Savings goal API and browser management page."""
import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse

from . import database
from .savings_schemas import SavingsContributionCreate, SavingsGoalCreate, SavingsGoalOut, SavingsGoalUpdate

router = APIRouter()

def _financial_week_start(today: date | None = None) -> date:
    today = today or date.today()
    with database.connect() as db:
        row = db.execute("SELECT financial_week_start FROM household_settings WHERE id = 1").fetchone()
    weekday = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
               "Friday": 4, "Saturday": 5, "Sunday": 6}.get(row[0] if row else "Friday", 4)
    return today - timedelta(days=(today.weekday() - weekday) % 7)


def _weekly_target(remaining_cents: int, target_date: date | None, schedule_start: date) -> int:
    if remaining_cents <= 0 or target_date is None or target_date < schedule_start:
        return 0
    weeks = max(1, ((target_date - schedule_start).days // 7) + 1)
    return (remaining_cents + weeks - 1) // weeks


def _goal_out(row) -> dict:
    item = dict(row)
    item["active"] = bool(item["active"])
    return item


@router.get("/api/savings-goals", response_model=list[SavingsGoalOut])
def list_savings_goals():
    with database.connect() as db:
        rows = db.execute(
            """SELECT id, name, target_amount_cents, current_balance_cents,
                      target_date, priority, weekly_target_cents, contribution_per_period_cents, last_contribution_date, schedule_start_date, active, created_at
               FROM savings_goals ORDER BY priority, name COLLATE NOCASE, id"""
        ).fetchall()
    return [_goal_out(row) for row in rows]


@router.post(
    "/api/savings-goals",
    response_model=SavingsGoalOut,
    status_code=status.HTTP_201_CREATED,
)
def create_savings_goal(body: SavingsGoalCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name cannot be blank")
    schedule_start = _financial_week_start()
    weekly_target = (_weekly_target(max(0, body.target_amount_cents - body.current_balance_cents), body.target_date, schedule_start) if body.target_date else body.weekly_target_cents)
    try:
        with database.connect() as db:
            cursor = db.execute(
                """INSERT INTO savings_goals
                   (name, target_amount_cents, current_balance_cents, target_date, priority,
                    contribution_per_period_cents, weekly_target_cents, schedule_start_date, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, body.target_amount_cents, body.current_balance_cents,
                 body.target_date.isoformat() if body.target_date else None,
                 body.priority, body.contribution_per_period_cents, weekly_target,
                 schedule_start.isoformat(), int(body.active)),
            )
            row = db.execute(
                """SELECT id, name, target_amount_cents, current_balance_cents,
                          target_date, priority, weekly_target_cents, contribution_per_period_cents, schedule_start_date, active, created_at
                   FROM savings_goals WHERE id = ?""", (cursor.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Savings goal violates a database constraint") from exc
    return _goal_out(row)


@router.patch("/api/savings-goals/{goal_id}", response_model=SavingsGoalOut)
def update_savings_goal(goal_id: int, body: SavingsGoalUpdate):
    updates = body.model_dump(exclude_unset=True)
    if "name" in updates:
        updates["name"] = updates["name"].strip()
        if not updates["name"]:
            raise HTTPException(status_code=422, detail="name cannot be blank")
    if "target_date" in updates and updates["target_date"] is not None:
        updates["target_date"] = updates["target_date"].isoformat()
    if "schedule_start_date" in updates and updates["schedule_start_date"] is not None:
        updates["schedule_start_date"] = updates["schedule_start_date"].isoformat()
    if "active" in updates:
        updates["active"] = int(updates["active"])
    if not updates:
        raise HTTPException(status_code=422, detail="no fields to update")
    allowed = {"name", "target_amount_cents", "current_balance_cents", "target_date", "priority", "weekly_target_cents", "contribution_per_period_cents", "schedule_start_date", "active"}
    if not set(updates) <= allowed:
        raise HTTPException(status_code=422, detail="unsupported field")
    try:
        with database.connect() as db:
            existing = db.execute("SELECT * FROM savings_goals WHERE id = ?", (goal_id,)).fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Savings goal not found")
            target_value = updates.get("target_date", existing["target_date"])
            target_date = date.fromisoformat(target_value) if target_value else None
            if target_date is not None and any(key in updates for key in ("target_amount_cents", "current_balance_cents", "target_date")):
                target_amount = updates.get("target_amount_cents", existing["target_amount_cents"])
                current_balance = updates.get("current_balance_cents", existing["current_balance_cents"])
                schedule_start = _financial_week_start()
                updates["schedule_start_date"] = schedule_start.isoformat()
                updates["weekly_target_cents"] = _weekly_target(max(0, target_amount - current_balance), target_date, schedule_start)
            elif target_date is None and ("target_date" in updates or "weekly_target_cents" in updates):
                # Start the undated weekly schedule from the current financial
                # week when its amount is set or its deadline is cleared.
                updates["schedule_start_date"] = _financial_week_start().isoformat()
                if "target_date" in updates and "weekly_target_cents" not in updates:
                    updates["weekly_target_cents"] = existing["weekly_target_cents"]
            assignments = ", ".join(f"{key} = ?" for key in updates)
            db.execute(
                f"UPDATE savings_goals SET {assignments} WHERE id = ?",
                (*updates.values(), goal_id),
            )
            row = db.execute(
                """SELECT id, name, target_amount_cents, current_balance_cents,
                          target_date, priority, weekly_target_cents, contribution_per_period_cents, last_contribution_date,
                          schedule_start_date, active, created_at
                   FROM savings_goals WHERE id = ?""", (goal_id,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Savings goal update violates a database constraint") from exc
    return _goal_out(row)



@router.post("/api/savings-goals/{goal_id}/contributions", status_code=status.HTTP_201_CREATED)
def record_savings_contribution(goal_id: int, body: SavingsContributionCreate):
    """Record an actual savings contribution in the internal cash ledger."""
    contribution_date = body.contribution_date or date.today()
    if contribution_date > date.today():
        raise HTTPException(status_code=422, detail="Actual savings contributions cannot be dated in the future.")
    marker = f"cashflow_savings_goal_id={goal_id};"
    with database.connect() as db:
        goal = db.execute(
            "SELECT id, name, current_balance_cents, last_contribution_date FROM savings_goals WHERE id = ? AND active = 1",
            (goal_id,),
        ).fetchone()
        if goal is None:
            raise HTTPException(status_code=404, detail="Active savings goal not found")
        if contribution_date == date.today():
            from .pay_period_forecast import transaction_bill_reservation_impact_from_forecast
            from .pay_period_service import build_pay_period_forecast

            forecast = build_pay_period_forecast(db, date.today())
            for period in forecast["periods"]:
                if period["start_date"] <= contribution_date.isoformat() <= period["end_date"]:
                    period["savings"] = [
                        item for item in period.get("savings", [])
                        if item.get("goal_id") != goal_id
                    ]
                    break
            impact = transaction_bill_reservation_impact_from_forecast(
                forecast, body.amount_cents, contribution_date.isoformat()
            )
            if impact["requires_confirmation"] and not body.confirm_reserved_bill_impact:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "This savings contribution would newly underfund scheduled bills.",
                        "underfunded_bills": impact["underfunded_bills"],
                        "total_bill_shortfall_cents": impact["total_bill_shortfall_cents"],
                    },
                )
        new_balance = int(goal["current_balance_cents"]) + body.amount_cents
        last_date = max(
            filter(None, (goal["last_contribution_date"], contribution_date.isoformat())),
            default=contribution_date.isoformat(),
        )
        db.execute(
            "UPDATE savings_goals SET current_balance_cents = ?, last_contribution_date = ? WHERE id = ?",
            (new_balance, last_date, goal_id),
        )
        db.execute(
            "INSERT OR IGNORE INTO categories (name, transaction_type) VALUES ('savings', 'expense')"
        )
        cursor = db.execute(
            """INSERT INTO transactions
               (transaction_date, category, transaction_type, description, amount_cents, notes)
               VALUES (?, 'savings', 'expense', ?, ?, ?)""",
            (contribution_date.isoformat(), f"Savings contribution — {goal['name']}",
             -body.amount_cents, marker),
        )
        row = db.execute(
            """SELECT id, name, target_amount_cents, current_balance_cents,
                      target_date, priority, weekly_target_cents, contribution_per_period_cents,
                      last_contribution_date, schedule_start_date, active, created_at
               FROM savings_goals WHERE id = ?""", (goal_id,)
        ).fetchone()
    return {"goal": _goal_out(row), "transaction_id": cursor.lastrowid}


@router.delete("/api/savings-goals/{goal_id}")
def delete_savings_goal(goal_id: int):
    with database.connect() as db:
        cursor = db.execute("DELETE FROM savings_goals WHERE id = ?", (goal_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Savings goal not found")
    return {"deleted": True, "id": goal_id}


@router.get("/savings", response_class=HTMLResponse)
def savings_page():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Savings Goals · CashFlow Engine</title>
<style>
:root{font-family:system-ui,sans-serif;color:#172033;background:#f3f6fa}*{box-sizing:border-box}
body{margin:0;padding:24px 14px 48px}main{max-width:1000px;margin:auto}a{color:#0e7490;font-weight:650;text-decoration:none}
h1{color:#155e75;margin:8px 0}.muted{color:#5c687a;line-height:1.5}
.card{background:white;padding:22px;border-radius:16px;box-shadow:0 8px 28px #172b4d0d;margin-top:18px}
form{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:12px;align-items:end}
label{display:block;font-weight:650;font-size:.9rem;margin-bottom:5px}
input{width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font:inherit;background:white}
button{border:0;border-radius:8px;padding:10px 12px;background:#0e7490;color:white;font-weight:700;cursor:pointer}
button:disabled{opacity:.55}.toggle{background:#475569;margin-top:10px}
.status{padding:12px;border-radius:9px;background:#e0f2fe;color:#075985}.error{background:#fee2e2;color:#991b1b}.status[hidden]{display:none}
.list{padding:0;list-style:none}.list li{padding:15px 0;border-top:1px solid #e2e8f0;overflow-wrap:anywhere}
.title{font-weight:700}.detail{color:#5c687a;font-size:.9rem;margin-top:4px}.progress{height:8px;background:#e2e8f0;border-radius:8px;overflow:hidden;margin-top:8px}
.fill{height:100%;background:#0e7490}
</style></head><body><main>
<a href="/">← Dashboard</a><h1>Savings Goals</h1>
<p class="muted">Set a target date to calculate the contribution needed each pay period, or choose a fixed per-period amount for ongoing savings. Contributions are forecast only; nothing moves automatically.</p>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<section class="card"><h2>Add a savings goal</h2>
<form id="goal-form">
<div><label for="goal-name">Goal name</label><input id="goal-name" name="name" maxlength="120" required placeholder="Emergency fund, vacation"></div>
<div><label for="goal-target">Goal amount ($, optional)</label><input id="goal-target" name="target" type="number" min="0" step="0.01" value="0"><small class="muted">Use $0 for ongoing general savings with no finish line.</small></div>
<div><label for="goal-current">Already saved ($)</label><input id="goal-current" name="current" type="number" min="0" step="0.01" value="0" required></div>
<div><label for="goal-date">Target date (optional)</label><input id="goal-date" name="target_date" type="date"></div>
<div><label for="goal-priority">Priority (lower is sooner)</label><input id="goal-priority" name="priority" type="number" min="0" max="10000" step="1" value="100" required></div>
<div><label for="goal-contribution">Ongoing contribution per pay period ($)</label><input id="goal-contribution" name="contribution_per_period" type="number" min="0" step="0.01" value="0" required><small class="muted">Used for ongoing goals without a deadline. Dated goals calculate the amount needed from the remaining target and remaining pay periods.</small></div>
<div><button type="submit">Save goal</button></div>
</form></section>
<section class="card"><h2>Your goals</h2><ul id="goals" class="list"><li>Loading goals…</li></ul></section>
<section class="card"><h2>How contributions are planned</h2><p class="muted">Deadline goals are recalculated each pay period using the remaining target and remaining pay periods. Ongoing goals use the fixed amount you set. Missed deadline contributions are spread across the remaining periods.</p></section>
</main>
<script>
const box=document.getElementById("status");
function message(text,error=false){box.textContent=text;box.classList.toggle("error",error);box.hidden=false;}
function cents(value){const n=Number(value);if(!Number.isFinite(n)||n<0)throw Error("Enter a valid non-negative amount.");return Math.round((n+Number.EPSILON)*100);}
function dollars(value){return new Intl.NumberFormat(undefined,{style:"currency",currency:"USD"}).format(value/100);}
async function api(url,options={}){const r=await fetch(url,{headers:{"Content-Type":"application/json"},...options});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"Please check your entries.");return d;}
function el(tag,cls,txt){const n=document.createElement(tag);if(cls)n.className=cls;n.textContent=txt;return n;}
async function refresh(){const goals=await api("/api/savings-goals");const list=document.getElementById("goals");list.replaceChildren();if(!goals.length){list.appendChild(el("li","muted","No savings goals saved yet."));} 
for(const g of goals){const li=el("li");li.appendChild(el("div","title",g.name+" · "+dollars(g.current_balance_cents)+(g.target_amount_cents>0?" of "+dollars(g.target_amount_cents):" saved · ongoing")));
const pct=g.target_amount_cents===0?null:Math.min(100,Math.round(g.current_balance_cents/g.target_amount_cents*100));
li.appendChild(el("div","detail",(pct===null?"Ongoing savings":pct+"% saved")+" · "+(g.target_date?"Deadline contribution recalculated each pay period · target "+g.target_date:dollars(g.contribution_per_period_cents||0)+" per pay period")+(g.last_contribution_date?" · last contribution "+g.last_contribution_date:"")+(g.active?"":" · inactive")));
if(pct!==null){const track=el("div","progress");track.setAttribute("aria-label",pct+" percent saved");const fill=el("div","fill","");fill.style.width=pct+"%";track.appendChild(fill);li.appendChild(track);}
if(g.active){const contribute=el("button","toggle","Record contribution");contribute.type="button";contribute.addEventListener("click",async()=>{const amount=prompt("Actual contribution amount in dollars:", "0.00");if(amount===null)return;const payload={amount_cents:cents(amount),confirm_reserved_bill_impact:false};const send=()=>fetch("/api/savings-goals/"+g.id+"/contributions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});try{let response=await send();let data=await response.json();if(response.status===409&&data.detail&&data.detail.underfunded_bills){const lines=data.detail.underfunded_bills.map(b=>b.name+" ("+b.due_date+"): $"+(b.unfunded_cents/100).toFixed(2)).join("\\n");if(!confirm("This contribution would underfund scheduled bills:\\n\\n"+lines+"\\n\\nRecord the contribution anyway?"))return;payload.confirm_reserved_bill_impact=true;response=await send();data=await response.json();}if(!response.ok)throw new Error((data.detail&&data.detail.message)||data.detail||"Could not record contribution.");await refresh();message("Savings contribution recorded in the ledger and goal balance.");}catch(e){message(e.message,true);}});li.appendChild(contribute);}
const edit=el("button","toggle","Edit goal");edit.type="button";edit.addEventListener("click",async()=>{const target=prompt("Target amount in dollars:",(g.target_amount_cents/100).toFixed(2));if(target===null)return;const current=prompt("Amount already saved in dollars:",(g.current_balance_cents/100).toFixed(2));if(current===null)return;const priority=prompt("Priority (lower is sooner):",String(g.priority));if(priority===null)return;const targetDate=prompt("Target date YYYY-MM-DD (leave blank for ongoing savings):",g.target_date||"");if(targetDate===null)return;const contribution=prompt("Ongoing contribution per pay period in dollars (used only without a target date):",((g.contribution_per_period_cents||0)/100).toFixed(2));if(contribution===null)return;try{await api("/api/savings-goals/"+g.id,{method:"PATCH",body:JSON.stringify({target_amount_cents:cents(target),current_balance_cents:cents(current),priority:Number(priority),contribution_per_period_cents:cents(contribution),target_date:targetDate||null})});await refresh();message("Savings goal updated.");}catch(e){message(e.message,true);}});li.appendChild(edit);
const toggle=el("button","toggle",g.active?"Deactivate":"Reactivate");toggle.type="button";toggle.addEventListener("click",async()=>{toggle.disabled=true;try{await api("/api/savings-goals/"+g.id,{method:"PATCH",body:JSON.stringify({active:!g.active})});await refresh();message("Savings goal status updated.");}catch(e){message(e.message,true);toggle.disabled=false;}});li.appendChild(toggle);
const remove=el("button","toggle","Delete goal");remove.type="button";remove.addEventListener("click",async()=>{if(!confirm("Permanently delete savings goal "+g.name+"?"))return;remove.disabled=true;try{await api("/api/savings-goals/"+g.id,{method:"DELETE"});await refresh();message("Savings goal deleted.");}catch(e){message(e.message,true);remove.disabled=false;}});li.appendChild(remove);list.appendChild(li);}
}
document.getElementById("goal-form").addEventListener("submit",async(e)=>{e.preventDefault();const f=e.currentTarget;const b=f.querySelector("button");b.disabled=true;try{await api("/api/savings-goals",{method:"POST",body:JSON.stringify({name:f.elements.name.value.trim(),target_amount_cents:cents(f.elements.target.value),current_balance_cents:cents(f.elements.current.value),target_date:f.elements.target_date.value||null,priority:Number(f.elements.priority.value),contribution_per_period_cents:cents(f.elements.contribution_per_period.value)})});f.reset();f.elements.current.value="0";f.elements.priority.value="100";f.elements.contribution_per_period.value="0";await refresh();message("Savings goal saved.");}catch(err){message(err.message,true);}finally{b.disabled=false;}});
refresh().catch(e=>message("Could not load savings goals: "+e.message,true));
</script></body></html>"""

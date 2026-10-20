"""Savings goal API and browser management page."""
import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse

from . import database
from .savings_schemas import SavingsGoalCreate, SavingsGoalOut, SavingsGoalUpdate

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
                      target_date, priority, weekly_target_cents, schedule_start_date, active, created_at
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
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (name, body.target_amount_cents, body.current_balance_cents,
                 body.target_date.isoformat() if body.target_date else None,
                 body.priority, weekly_target,
                 schedule_start.isoformat(), int(body.active)),
            )
            row = db.execute(
                """SELECT id, name, target_amount_cents, current_balance_cents,
                          target_date, priority, weekly_target_cents, schedule_start_date, active, created_at
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
    allowed = {"name", "target_amount_cents", "current_balance_cents", "target_date", "priority", "weekly_target_cents", "schedule_start_date", "active"}
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
                          target_date, priority, weekly_target_cents,
                          schedule_start_date, active, created_at
                   FROM savings_goals WHERE id = ?""", (goal_id,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Savings goal update violates a database constraint") from exc
    return _goal_out(row)


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
<p class="muted">Set a weekly savings amount for each goal. Add a target date to calculate the weekly amount automatically, or leave the date blank for ongoing general savings. Nothing moves automatically.</p>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<section class="card"><h2>Add a savings goal</h2>
<form id="goal-form">
<div><label for="goal-name">Goal name</label><input id="goal-name" name="name" maxlength="120" required placeholder="Emergency fund, vacation"></div>
<div><label for="goal-target">Goal amount ($, optional)</label><input id="goal-target" name="target" type="number" min="0" step="0.01" value="0"><small class="muted">Use $0 for ongoing general savings with no finish line.</small></div>
<div><label for="goal-current">Already saved ($)</label><input id="goal-current" name="current" type="number" min="0" step="0.01" value="0" required></div>
<div><label for="goal-date">Target date (optional)</label><input id="goal-date" name="target_date" type="date"></div>
<div><label for="goal-priority">Priority (lower is sooner)</label><input id="goal-priority" name="priority" type="number" min="0" max="10000" step="1" value="100" required></div>
<div><label for="goal-weekly">Weekly savings amount ($)</label><input id="goal-weekly" name="weekly_target" type="number" min="0" step="0.01" value="0" required><small class="muted">For goals with a target date, this is calculated automatically from the amount remaining and deadline. Without a date, this is your chosen amount per financial week.</small></div>
<div><button type="submit">Save goal</button></div>
</form></section>
<section class="card"><h2>Your goals</h2><ul id="goals" class="list"><li>Loading goals…</li></ul></section>
<section class="card"><h2>This week's savings plan</h2><p class="muted">Weekly targets are based on deadlines, with missed amounts carried forward. Only the safe funded amount is recommended; no money moves automatically.</p><ul id="weekly-plan" class="list"><li>Loading weekly plan…</li></ul></section>
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
li.appendChild(el("div","detail",(pct===null?"Ongoing savings":pct+"% saved")+" · "+dollars(g.weekly_target_cents||0)+" per financial week"+(g.target_date?" · target "+g.target_date:" · no target date")+(g.active?"":" · inactive")));
if(pct!==null){const track=el("div","progress");track.setAttribute("aria-label",pct+" percent saved");const fill=el("div","fill","");fill.style.width=pct+"%";track.appendChild(fill);li.appendChild(track);}
const edit=el("button","toggle","Edit goal");edit.type="button";edit.addEventListener("click",async()=>{const target=prompt("Target amount in dollars:",(g.target_amount_cents/100).toFixed(2));if(target===null)return;const current=prompt("Amount already saved in dollars:",(g.current_balance_cents/100).toFixed(2));if(current===null)return;const priority=prompt("Priority (lower is sooner):",String(g.priority));if(priority===null)return;const targetDate=prompt("Target date YYYY-MM-DD (leave blank for ongoing weekly savings):",g.target_date||"");if(targetDate===null)return;let weeklyTarget=g.weekly_target_cents||0;if(!targetDate){const weekly=prompt("Amount to save each financial week in dollars:",(weeklyTarget/100).toFixed(2));if(weekly===null)return;weeklyTarget=cents(weekly);}try{await api("/api/savings-goals/"+g.id,{method:"PATCH",body:JSON.stringify({target_amount_cents:cents(target),current_balance_cents:cents(current),priority:Number(priority),weekly_target_cents:weeklyTarget,target_date:targetDate||null})});await refresh();message("Savings goal updated.");}catch(e){message(e.message,true);}});li.appendChild(edit);
const toggle=el("button","toggle",g.active?"Deactivate":"Reactivate");toggle.type="button";toggle.addEventListener("click",async()=>{toggle.disabled=true;try{await api("/api/savings-goals/"+g.id,{method:"PATCH",body:JSON.stringify({active:!g.active})});await refresh();message("Savings goal status updated.");}catch(e){message(e.message,true);toggle.disabled=false;}});li.appendChild(toggle);
const remove=el("button","toggle","Delete goal");remove.type="button";remove.addEventListener("click",async()=>{if(!confirm("Permanently delete savings goal "+g.name+"?"))return;remove.disabled=true;try{await api("/api/savings-goals/"+g.id,{method:"DELETE"});await refresh();message("Savings goal deleted.");}catch(e){message(e.message,true);remove.disabled=false;}});li.appendChild(remove);list.appendChild(li);}
const weeklyList=document.getElementById("weekly-plan");weeklyList.replaceChildren();
const report=await api("/api/cash-flow/weekly");
const plans=report.savings_plan||[];
if(!plans.length){weeklyList.appendChild(el("li","muted","No active weekly savings goals. Add a goal and choose a weekly amount."));}
for(const p of plans){const li=el("li");li.appendChild(el("div","title",p.goal_name+" · weekly target "+dollars(p.weekly_target_cents)));
li.appendChild(el("div","detail","This week: "+dollars(p.planned_this_week_cents)+" still planned · "+dollars(p.funded_this_week_cents||0)+" safely fundable · "+dollars(p.unfunded_this_week_cents||0)+" not currently affordable"));
li.appendChild(el("div","detail","Regular target: "+dollars(p.regular_target_cents||p.weekly_target_cents)+" · catch-up needed: "+dollars(p.catch_up_cents||0)+" · recorded contributions: "+dollars(p.recorded_this_week_cents||0)));
li.appendChild(el("div","detail","Opening arrears: "+dollars(p.outstanding_arrears_cents??p.arrears_cents)+" · arrears remaining after recorded contributions: "+dollars(p.arrears_after_recorded_contributions_cents??p.arrears_cents)+(p.target_date?" · goal remaining: "+dollars(p.remaining_cents)+" · needed weekly to deadline: "+dollars(p.required_weekly_to_deadline_cents):" · ongoing goal with no deadline")));
if(p.deadline_warning){li.appendChild(el("div","detail",p.target_date?"Warning: this goal is behind schedule or this week's full target is not safely affordable.":"Warning: this week's full target is not currently safely affordable."));}
weeklyList.appendChild(li);}}
document.getElementById("goal-form").addEventListener("submit",async(e)=>{e.preventDefault();const f=e.currentTarget;const b=f.querySelector("button");b.disabled=true;try{await api("/api/savings-goals",{method:"POST",body:JSON.stringify({name:f.elements.name.value.trim(),target_amount_cents:cents(f.elements.target.value),current_balance_cents:cents(f.elements.current.value),target_date:f.elements.target_date.value||null,priority:Number(f.elements.priority.value),weekly_target_cents:cents(f.elements.weekly_target.value)})});f.reset();f.elements.current.value="0";f.elements.priority.value="100";f.elements.weekly_target.value="0";await refresh();message("Savings goal saved.");}catch(err){message(err.message,true);}finally{b.disabled=false;}});
refresh().catch(e=>message("Could not load savings goals: "+e.message,true));
</script></body></html>"""

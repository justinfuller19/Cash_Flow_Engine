"""Savings goal API and browser management page."""
import sqlite3

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse

from . import database
from .savings_schemas import SavingsGoalCreate, SavingsGoalOut, SavingsGoalUpdate

router = APIRouter()


def _goal_out(row) -> dict:
    item = dict(row)
    item["active"] = bool(item["active"])
    return item


@router.get("/api/savings-goals", response_model=list[SavingsGoalOut])
def list_savings_goals():
    with database.connect() as db:
        rows = db.execute(
            """SELECT id, name, target_amount_cents, current_balance_cents,
                      target_date, priority, contribution_per_period_cents, active, created_at
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
    try:
        with database.connect() as db:
            cursor = db.execute(
                """INSERT INTO savings_goals
                   (name, target_amount_cents, current_balance_cents, target_date, priority, contribution_per_period_cents, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, body.target_amount_cents, body.current_balance_cents,
                 body.target_date.isoformat() if body.target_date else None,
                 body.priority, body.contribution_per_period_cents, int(body.active)),
            )
            row = db.execute(
                """SELECT id, name, target_amount_cents, current_balance_cents,
                          target_date, priority, contribution_per_period_cents, active, created_at
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
    if "active" in updates:
        updates["active"] = int(updates["active"])
    if not updates:
        raise HTTPException(status_code=422, detail="no fields to update")
    allowed = {"name", "target_amount_cents", "current_balance_cents", "target_date", "priority", "contribution_per_period_cents", "active"}
    if not set(updates) <= allowed:
        raise HTTPException(status_code=422, detail="unsupported field")
    try:
        with database.connect() as db:
            if db.execute("SELECT id FROM savings_goals WHERE id = ?", (goal_id,)).fetchone() is None:
                raise HTTPException(status_code=404, detail="Savings goal not found")
            assignments = ", ".join(f"{key} = ?" for key in updates)
            db.execute(
                f"UPDATE savings_goals SET {assignments} WHERE id = ?",
                (*updates.values(), goal_id),
            )
            row = db.execute(
                """SELECT id, name, target_amount_cents, current_balance_cents,
                          target_date, priority, contribution_per_period_cents, active, created_at
                   FROM savings_goals WHERE id = ?""", (goal_id,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Savings goal update violates a database constraint") from exc
    return _goal_out(row)


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
<p class="muted">Track what you're saving toward, what you've already set aside, and target dates. Goals are planning records; contributions are not moved automatically.</p>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<section class="card"><h2>Add a savings goal</h2>
<form id="goal-form">
<div><label for="goal-name">Goal name</label><input id="goal-name" name="name" maxlength="120" required placeholder="Emergency fund, vacation"></div>
<div><label for="goal-target">Target amount ($)</label><input id="goal-target" name="target" type="number" min="0" step="0.01" required></div>
<div><label for="goal-current">Already saved ($)</label><input id="goal-current" name="current" type="number" min="0" step="0.01" value="0" required></div>
<div><label for="goal-date">Target date (optional)</label><input id="goal-date" name="target_date" type="date"></div>
<div><label for="goal-priority">Priority (lower is sooner)</label><input id="goal-priority" name="priority" type="number" min="0" max="10000" step="1" value="100" required></div>
<div><label for="goal-contribution">Planned contribution per payday ($)</label><input id="goal-contribution" name="contribution" type="number" min="0" step="0.01" value="0" required></div>
<div><button type="submit">Save goal</button></div>
</form></section>
<section class="card"><h2>Your goals</h2><ul id="goals" class="list"><li>Loading goals…</li></ul></section>
</main>
<script>
const box=document.getElementById("status");
function message(text,error=false){box.textContent=text;box.classList.toggle("error",error);box.hidden=false;}
function cents(value){const n=Number(value);if(!Number.isFinite(n)||n<0)throw Error("Enter a valid non-negative amount.");return Math.round((n+Number.EPSILON)*100);}
function dollars(value){return new Intl.NumberFormat(undefined,{style:"currency",currency:"USD"}).format(value/100);}
async function api(url,options={}){const r=await fetch(url,{headers:{"Content-Type":"application/json"},...options});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"Please check your entries.");return d;}
function el(tag,cls,txt){const n=document.createElement(tag);if(cls)n.className=cls;n.textContent=txt;return n;}
async function refresh(){const goals=await api("/api/savings-goals");const list=document.getElementById("goals");list.replaceChildren();if(!goals.length){list.appendChild(el("li","muted","No savings goals saved yet."));return;}
for(const g of goals){const li=el("li");li.appendChild(el("div","title",g.name+" · "+dollars(g.current_balance_cents)+" of "+dollars(g.target_amount_cents)));
const pct=g.target_amount_cents===0?100:Math.min(100,Math.round(g.current_balance_cents/g.target_amount_cents*100));
li.appendChild(el("div","detail",pct+"% saved · priority "+g.priority+" · plan "+dollars(g.contribution_per_period_cents)+" per payday"+(g.target_date?" · target "+g.target_date:"")+(g.active?"":" · inactive")));
const track=el("div","progress");track.setAttribute("aria-label",pct+" percent saved");const fill=el("div","fill","");fill.style.width=pct+"%";track.appendChild(fill);li.appendChild(track);
const edit=el("button","toggle","Edit goal");edit.type="button";edit.addEventListener("click",async()=>{const target=prompt("Target amount in dollars:",(g.target_amount_cents/100).toFixed(2));if(target===null)return;const current=prompt("Amount already saved in dollars:",(g.current_balance_cents/100).toFixed(2));if(current===null)return;const priority=prompt("Priority (lower is sooner):",String(g.priority));if(priority===null)return;const contribution=prompt("Planned contribution per payday in dollars:",(g.contribution_per_period_cents/100).toFixed(2));if(contribution===null)return;const targetDate=prompt("Target date YYYY-MM-DD (leave blank to clear):",g.target_date||"");if(targetDate===null)return;try{await api("/api/savings-goals/"+g.id,{method:"PATCH",body:JSON.stringify({target_amount_cents:cents(target),current_balance_cents:cents(current),priority:Number(priority),contribution_per_period_cents:cents(contribution),target_date:targetDate||null})});await refresh();message("Savings goal updated.");}catch(e){message(e.message,true);}});li.appendChild(edit);
const toggle=el("button","toggle",g.active?"Deactivate":"Reactivate");toggle.type="button";toggle.addEventListener("click",async()=>{toggle.disabled=true;try{await api("/api/savings-goals/"+g.id,{method:"PATCH",body:JSON.stringify({active:!g.active})});await refresh();message("Savings goal status updated.");}catch(e){message(e.message,true);toggle.disabled=false;}});li.appendChild(toggle);list.appendChild(li);}}
document.getElementById("goal-form").addEventListener("submit",async(e)=>{e.preventDefault();const f=e.currentTarget;const b=f.querySelector("button");b.disabled=true;try{await api("/api/savings-goals",{method:"POST",body:JSON.stringify({name:f.elements.name.value.trim(),target_amount_cents:cents(f.elements.target.value),current_balance_cents:cents(f.elements.current.value),target_date:f.elements.target_date.value||null,priority:Number(f.elements.priority.value),contribution_per_period_cents:cents(f.elements.contribution.value)})});f.reset();f.elements.current.value="0";f.elements.priority.value="100";f.elements.contribution.value="0";await refresh();message("Savings goal saved.");}catch(err){message(err.message,true);}finally{b.disabled=false;}});
refresh().catch(e=>message("Could not load savings goals: "+e.message,true));
</script></body></html>"""

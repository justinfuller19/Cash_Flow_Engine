"""Household dashboard summaries and payday checklist workflows."""
import sqlite3
from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from . import database
from .forecast import occurrences_between
from .checklist_schemas import ChecklistItemOut, ChecklistItemUpdate

router = APIRouter()


def _day_of_month(day: int, start: date) -> date:
    candidate = date(start.year, start.month, min(day, monthrange(start.year, start.month)[1]))
    if candidate < start:
        month_index = start.year * 12 + start.month
        year, month0 = divmod(month_index, 12)
        month = month0 + 1
        candidate = date(year, month, min(day, monthrange(year, month)[1]))
    return candidate


@router.get("/api/dashboard")
def dashboard_summary():
    today = date.today()
    next_90 = today + timedelta(days=90)
    next_30 = today + timedelta(days=30)
    with database.connect() as db:
        buffer_total = db.execute(
            "SELECT COALESCE(buffer_target_cents, 0) FROM household_settings WHERE id = 1"
        ).fetchone()
        buffer_total = buffer_total[0] if buffer_total else 0
        people_count = db.execute("SELECT COUNT(*) FROM persons WHERE active = 1").fetchone()[0]

        paydays = []
        rows = db.execute(
            """SELECT i.name, i.net_amount_cents, i.frequency, i.next_payday, p.name AS person_name
               FROM income_sources i JOIN persons p ON p.id = i.person_id
               WHERE i.active = 1 AND p.active = 1 ORDER BY i.next_payday, i.id"""
        ).fetchall()
        for row in rows:
            dates = occurrences_between(date.fromisoformat(row["next_payday"]), row["frequency"], today, next_90)
            if dates:
                paydays.append({
                    "name": f'{row["person_name"]} — {row["name"]}',
                    "amount_cents": row["net_amount_cents"],
                    "date": dates[0].isoformat(),
                })
        paydays.sort(key=lambda item: (item["date"], item["name"]))

        bills = []
        rows = db.execute(
            """SELECT id, name, amount_cents, category, frequency, next_due_date, autopay
               FROM bills WHERE active = 1 ORDER BY next_due_date, id"""
        ).fetchall()
        for row in rows:
            if not row["next_due_date"]:
                continue
            dates = occurrences_between(date.fromisoformat(row["next_due_date"]), row["frequency"], today, next_30)
            for due in dates:
                bills.append({
                    "id": row["id"], "name": row["name"], "amount_cents": row["amount_cents"],
                    "category": row["category"], "date": due.isoformat(), "autopay": bool(row["autopay"]),
                })
        bills.sort(key=lambda item: (item["date"], item["name"]))

        debts = []
        rows = db.execute(
            """SELECT id, name, balance_cents, minimum_payment_cents, next_due_date, due_day_of_month
               FROM credit_cards WHERE active = 1 ORDER BY next_due_date, id"""
        ).fetchall()
        for row in rows:
            anchor = date.fromisoformat(row["next_due_date"]) if row["next_due_date"] else None
            dates = occurrences_between(anchor, "monthly", today, next_30) if anchor else []
            due = dates[0] if dates else (_day_of_month(row["due_day_of_month"], today) if row["due_day_of_month"] else None)
            if due and due <= next_30:
                debts.append({
                    "id": row["id"], "name": row["name"], "balance_cents": row["balance_cents"],
                    "minimum_payment_cents": row["minimum_payment_cents"], "date": due.isoformat(),
                })
        debts.sort(key=lambda item: (item["date"], item["name"]))

        goals = []
        for row in db.execute(
            """SELECT id, name, target_amount_cents, current_balance_cents,
                      contribution_per_period_cents, target_date, priority
               FROM savings_goals WHERE active = 1 ORDER BY priority, name COLLATE NOCASE"""
        ).fetchall():
            target = row["target_amount_cents"]
            goals.append({
                "id": row["id"], "name": row["name"], "target_amount_cents": target,
                "current_balance_cents": row["current_balance_cents"],
                "contribution_per_period_cents": row["contribution_per_period_cents"],
                "target_date": row["target_date"],
                "progress_percent": 100 if target == 0 else min(100, round(row["current_balance_cents"] / target * 100)),
            })

        plans = db.execute(
            """SELECT id, period_start, period_end, total_available_cents,
                      allocated_cents, unfunded_cents, status
               FROM payday_plans ORDER BY plan_date DESC, id DESC LIMIT 3"""
        ).fetchall()
        month_start = today.replace(day=1).isoformat()
        month_income = db.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM transactions WHERE transaction_date >= ? AND transaction_type = 'income' AND amount_cents > 0",
            (month_start,),
        ).fetchone()[0]
        month_expenses = db.execute(
            "SELECT COALESCE(SUM(-amount_cents), 0) FROM transactions WHERE transaction_date >= ? AND transaction_type = 'expense' AND amount_cents < 0",
            (month_start,),
        ).fetchone()[0]
        recent_transactions = db.execute(
            """SELECT id, transaction_date, category, transaction_type, description, amount_cents, account_name
               FROM transactions ORDER BY transaction_date DESC, id DESC LIMIT 5"""
        ).fetchall()
        pending_count = db.execute(
            """SELECT COUNT(*) FROM plan_items pi JOIN payday_plans pp ON pp.id = pi.plan_id
               WHERE pi.status IN ('pending', 'attention') AND pp.status NOT IN ('completed', 'cancelled')"""
        ).fetchone()[0]

    return {
        "today": today.isoformat(),
        "active_household_members": people_count,
        "protected_buffers_cents": buffer_total,
        "next_paydays": paydays[:6],
        "upcoming_bills": bills[:8],
        "upcoming_debts": debts[:8],
        "savings_goals": goals[:6],
        "recent_plans": [dict(row) for row in plans],
        "month_income_cents": month_income,
        "month_expenses_cents": month_expenses,
        "recent_transactions": [dict(row) for row in recent_transactions],
        "pending_checklist_count": pending_count,
    }


@router.get("/api/checklist", response_model=list[ChecklistItemOut])
def list_checklist_items():
    with database.connect() as db:
        rows = db.execute(
            """SELECT pi.id, pi.plan_id, pp.period_start AS plan_period_start,
                      pp.period_end AS plan_period_end, pi.category, pi.name,
                      pi.requested_cents, pi.funded_cents, pi.unfunded_cents,
                      pi.due_date, pi.note, pi.status, pi.actual_amount_cents,
                      pi.actual_date
               FROM plan_items pi JOIN payday_plans pp ON pp.id = pi.plan_id
               WHERE pi.status IN ('pending', 'attention')
                 AND pp.status NOT IN ('completed', 'cancelled')
               ORDER BY COALESCE(pi.due_date, pp.period_start), pi.id"""
        ).fetchall()
    return [dict(row) for row in rows]


@router.patch("/api/plan-items/{item_id}", response_model=ChecklistItemOut)
def update_checklist_item(item_id: int, body: ChecklistItemUpdate):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="no fields to update")
    if "actual_date" in updates and updates["actual_date"] is not None:
        updates["actual_date"] = updates["actual_date"].isoformat()
    if "status" in updates:
        updates["is_completed"] = int(updates["status"] == "completed")
    allowed = {"status", "actual_amount_cents", "actual_date", "is_completed"}
    if not set(updates) <= allowed:
        raise HTTPException(status_code=422, detail="unsupported field")
    try:
        with database.connect() as db:
            if db.execute("SELECT id FROM plan_items WHERE id = ?", (item_id,)).fetchone() is None:
                raise HTTPException(status_code=404, detail="Checklist item not found")
            assignments = ", ".join(f"{key} = ?" for key in updates)
            db.execute(f"UPDATE plan_items SET {assignments} WHERE id = ?", (*updates.values(), item_id))
            row = db.execute(
                """SELECT pi.id, pi.plan_id, pp.period_start AS plan_period_start,
                          pp.period_end AS plan_period_end, pi.category, pi.name,
                          pi.requested_cents, pi.funded_cents, pi.unfunded_cents,
                          pi.due_date, pi.note, pi.status, pi.actual_amount_cents,
                          pi.actual_date
                   FROM plan_items pi JOIN payday_plans pp ON pp.id = pi.plan_id
                   WHERE pi.id = ?""", (item_id,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Checklist update violates a database constraint") from exc
    return dict(row)


@router.get("/checklist", response_class=HTMLResponse)
def checklist_page():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Payday Checklist · CashFlow Engine</title>
<style>
:root{font-family:system-ui,sans-serif;color:#172033;background:#f3f6fa}*{box-sizing:border-box}
body{margin:0;padding:24px 14px 48px}main{max-width:1050px;margin:auto}a{color:#0e7490;font-weight:650;text-decoration:none}
h1{color:#155e75;margin:8px 0}.muted{color:#5c687a;line-height:1.5}
.card{background:white;padding:20px;border-radius:16px;box-shadow:0 8px 28px #172b4d0d;margin-top:18px}
.item{padding:16px 0;border-top:1px solid #e2e8f0}.item:first-child{border-top:0}.title{font-weight:700}
.detail{color:#5c687a;font-size:.9rem;margin-top:4px}.controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,170px),1fr));gap:10px;align-items:end;margin-top:12px}
label{display:block;font-size:.9rem;font-weight:650;margin-bottom:5px}input,select{width:100%;padding:9px;border:1px solid #cbd5e1;border-radius:8px;font:inherit}
button{border:0;border-radius:8px;padding:10px 12px;background:#0e7490;color:white;font-weight:700;cursor:pointer}
button:disabled{opacity:.55}.status{padding:12px;border-radius:9px;background:#e0f2fe;color:#075985}.error{background:#fee2e2;color:#991b1b}.status[hidden]{display:none}
</style></head><body><main><a href="/">← Dashboard</a><h1>Payday Checklist</h1>
<p class="muted">Record what you actually completed. Saving a status or actual amount only updates your records; it never sends a payment.</p>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<section class="card"><div id="items">Loading checklist…</div><nav id="checklist-pagination" aria-label="Checklist pages" class="controls"></nav></section></main>
<script>
const box=document.getElementById("status");
function message(text,error=false){box.textContent=text;box.classList.toggle("error",error);box.hidden=false;}
function dollars(cents){return new Intl.NumberFormat(undefined,{style:"currency",currency:"USD"}).format(cents/100);}
function cents(value){if(value==="")return null;const n=Number(value);if(!Number.isFinite(n)||n<0)throw Error("Actual amount must be non-negative.");return Math.round((n+Number.EPSILON)*100);}
async function api(url,options={}){const r=await fetch(url,{headers:{"Content-Type":"application/json"},...options});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"Please check your entries.");return d;}
function el(tag,cls,txt){const n=document.createElement(tag);if(cls)n.className=cls;n.textContent=txt;return n;}
const PAGE_SIZE=10;let checklistItems=[];let checklistPage=1;
function renderChecklistPage(){const root=document.getElementById("items");const nav=document.getElementById("checklist-pagination");root.replaceChildren();nav.replaceChildren();if(!checklistItems.length){root.appendChild(el("p","muted","Nothing needs attention right now. Generate a payday plan to create a checklist."));return;}
const pageCount=Math.ceil(checklistItems.length/PAGE_SIZE);checklistPage=Math.min(checklistPage,pageCount);const start=(checklistPage-1)*PAGE_SIZE;
for(const item of checklistItems.slice(start,start+PAGE_SIZE)){const card=el("div","item","");card.appendChild(el("div","title",item.name+" · planned "+dollars(item.funded_cents)));
card.appendChild(el("div","detail",item.category+" · plan "+item.plan_period_start+" to "+item.plan_period_end+(item.due_date?" · due "+item.due_date:"")+(item.unfunded_cents?" · planned shortfall "+dollars(item.unfunded_cents):"")));
const controls=el("div","controls","");const statusWrap=el("div","","");const statusLabel=el("label","","Status");const status=document.createElement("select");statusLabel.appendChild(status);for(const value of ["pending","completed","skipped","attention"]){const option=document.createElement("option");option.value=value;option.textContent=value[0].toUpperCase()+value.slice(1);status.appendChild(option);}status.value=item.status;statusWrap.appendChild(statusLabel);controls.appendChild(statusWrap);
const amountWrap=el("div","","");const amountLabel=el("label","","Actual amount ($)");const amount=document.createElement("input");amount.type="number";amount.min="0";amount.step="0.01";amount.placeholder="Optional";amount.value=item.actual_amount_cents===null?"":(item.actual_amount_cents/100).toFixed(2);amountLabel.appendChild(amount);amountWrap.appendChild(amountLabel);controls.appendChild(amountWrap);
const dateWrap=el("div","","");const dateLabel=el("label","","Actual date");const actualDate=document.createElement("input");actualDate.type="date";actualDate.value=item.actual_date||"";dateLabel.appendChild(actualDate);dateWrap.appendChild(dateLabel);controls.appendChild(dateWrap);
const save=el("button","","Save action");save.type="button";save.addEventListener("click",async()=>{save.disabled=true;try{const actual=cents(amount.value);await api("/api/plan-items/"+item.id,{method:"PATCH",body:JSON.stringify({status:status.value,actual_amount_cents:actual,actual_date:actualDate.value||null})});await refresh();message("Checklist action saved.");}catch(e){message(e.message,true);save.disabled=false;}});controls.appendChild(save);card.appendChild(controls);root.appendChild(card);}
const previous=el("button","","Previous");previous.type="button";previous.disabled=checklistPage===1;previous.addEventListener("click",()=>{checklistPage--;renderChecklistPage();});nav.appendChild(previous);
nav.appendChild(el("span","detail","Page "+checklistPage+" of "+pageCount+" · "+checklistItems.length+" actions"));
const next=el("button","","Next");next.type="button";next.disabled=checklistPage===pageCount;next.addEventListener("click",()=>{checklistPage++;renderChecklistPage();});nav.appendChild(next);}
async function refresh(){checklistItems=await api("/api/checklist");renderChecklistPage();}
refresh().catch(e=>message("Could not load checklist: "+e.message,true));
</script></body></html>"""

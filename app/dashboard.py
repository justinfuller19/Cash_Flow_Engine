"""Household dashboard summaries and payday checklist workflows."""
import sqlite3
from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from . import database
from .forecast import occurrences_between
from .cash_flow import weekly_cash_flow
from .checklist_schemas import ChecklistItemOut, ChecklistItemUpdate

router = APIRouter()


def _financial_week(today: date | None = None) -> tuple[date, date]:
    """Return the household's current configured seven-day financial week."""
    today = today or date.today()
    with database.connect() as db:
        settings = db.execute(
            "SELECT financial_week_start FROM household_settings WHERE id = 1"
        ).fetchone()
    weekday_name = settings["financial_week_start"] if settings else "Friday"
    weekday_index = {
        "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
        "Friday": 4, "Saturday": 5, "Sunday": 6,
    }.get(weekday_name, 4)
    start = today - timedelta(days=(today.weekday() - weekday_index) % 7)
    return start, start + timedelta(days=6)


def _ensure_automatic_checklist() -> int:
    """Create or refresh this week's checklist from the live safe cash-flow forecast."""
    start, end = _financial_week()
    report = weekly_cash_flow(start_date=start, end_date=end, forecast_weeks=13)
    actions: list[dict] = []

    for income in report.get("expected_income", []):
        actions.append({
            "category": "income", "name": "Confirm paycheck: " + income["name"],
            "requested_cents": income["amount_cents"], "funded_cents": income["amount_cents"],
            "unfunded_cents": 0, "due_date": income["date"],
            "note": "Expected income. Record the actual paycheck under Transactions to reconcile the forecast.",
        })

    for bill in report.get("bills", []):
        if bill["status"] != "due":
            continue
        actions.append({
            "category": "bill", "name": "Pay bill: " + bill["name"],
            "requested_cents": bill["amount_cents"], "funded_cents": bill["amount_cents"],
            "unfunded_cents": 0, "due_date": bill["due_date"],
            "note": "Scheduled bill. Record the payment under Transactions and link it to this bill.",
        })

    for debt in report.get("minimum_debt_payments", []):
        if debt["status"] != "due":
            continue
        actions.append({
            "category": "debt", "name": "Pay minimum: " + debt["name"],
            "requested_cents": debt["amount_cents"], "funded_cents": debt["amount_cents"],
            "unfunded_cents": 0, "due_date": debt["due_date"],
            "note": "Required minimum payment. Record the payment under Transactions and link it to this debt.",
        })

    for goal in report.get("savings_plan", []):
        requested = goal["planned_this_week_cents"]
        funded = goal.get("funded_this_week_cents", 0)
        if requested <= 0 and not goal.get("deadline_warning"):
            continue
        note = "Weekly savings target."
        if goal.get("arrears_cents", 0):
            note += " A previous weekly target was missed; see Savings Goals for the exact carryover."
        if goal.get("deadline_warning"):
            note += (
                " Warning: the goal may miss its target date at the current funding rate."
                if goal.get("target_date")
                else " Warning: this week's ongoing savings target is not fully affordable."
            )
        actions.append({
            "category": "savings", "name": "Save for: " + goal["goal_name"],
            "savings_goal_id": goal["goal_id"],
            "requested_cents": max(0, requested), "funded_cents": max(0, funded),
            "unfunded_cents": max(0, goal.get("unfunded_this_week_cents", requested - funded)),
            "due_date": end.isoformat(), "note": note,
        })

    income_total = report.get("expected_income_cents", 0) + report.get("recorded_income_cents", 0)
    total_available = max(0, report.get("starting_cash_cents", 0) + income_total)
    allocated = sum(action["funded_cents"] for action in actions)
    unfunded = sum(action["unfunded_cents"] for action in actions)

    with database.connect() as db:
        plan = db.execute(
            """SELECT id FROM payday_plans
               WHERE is_auto_generated = 1 AND period_start = ? AND period_end = ?
                 AND status = 'active'
               ORDER BY id DESC LIMIT 1""",
            (start.isoformat(), end.isoformat()),
        ).fetchone()
        if plan is None:
            # The partial unique index makes this safe if two requests both
            # observed that no plan existed for the week. INSERT OR IGNORE lets
            # the losing request reuse the winner's plan instead of creating a
            # second checklist (or returning a uniqueness error).
            db.execute(
                """INSERT OR IGNORE INTO payday_plans
                   (plan_date, period_start, period_end, starting_cash_cents, debt_strategy,
                    status, is_auto_generated, income_cents, total_available_cents,
                    allocated_cents, unallocated_cents, unfunded_cents)
                   VALUES (?, ?, ?, ?, 'avalanche', 'active', 1, ?, ?, ?, ?, ?)""",
                (date.today().isoformat(), start.isoformat(), end.isoformat(),
                 report.get("starting_cash_cents", 0), income_total, total_available,
                 allocated, max(0, total_available - allocated), unfunded),
            )
            plan = db.execute(
                """SELECT id FROM payday_plans
                   WHERE is_auto_generated = 1 AND period_start = ? AND period_end = ?
                     AND status = 'active'
                   ORDER BY id DESC LIMIT 1""",
                (start.isoformat(), end.isoformat()),
            ).fetchone()
        plan_id = plan["id"]
        db.execute(
            """UPDATE payday_plans SET starting_cash_cents = ?, income_cents = ?,
               total_available_cents = ?, allocated_cents = ?, unallocated_cents = ?,
               unfunded_cents = ? WHERE id = ?""",
            (report.get("starting_cash_cents", 0), income_total, total_available,
             allocated, max(0, total_available - allocated), unfunded, plan_id),
        )

        existing_rows = db.execute(
            "SELECT * FROM plan_items WHERE plan_id = ? ORDER BY id", (plan_id,)
        ).fetchall()
        existing_by_key = {
            (row["category"], row["name"], row["due_date"]): row for row in existing_rows
        }
        wanted_keys = set()
        for action in actions:
            key = (action["category"], action["name"], action["due_date"])
            wanted_keys.add(key)
            old = existing_by_key.get(key)
            if old is None:
                db.execute(
                    """INSERT INTO plan_items
                       (plan_id, category, name, requested_cents, funded_cents,
                        unfunded_cents, due_date, note, savings_goal_id, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                    (plan_id, action["category"], action["name"], action["requested_cents"],
                     action["funded_cents"], action["unfunded_cents"], action["due_date"],
                     action["note"], action.get("savings_goal_id")),
                )
            elif old["status"] not in ("completed", "skipped"):
                db.execute(
                    """UPDATE plan_items SET requested_cents = ?, funded_cents = ?,
                       unfunded_cents = ?, note = ?, savings_goal_id = ? WHERE id = ?""",
                    (action["requested_cents"], action["funded_cents"],
                     action["unfunded_cents"], action["note"],
                     action.get("savings_goal_id"), old["id"]),
                )

        for old in existing_rows:
            key = (old["category"], old["name"], old["due_date"])
            if key not in wanted_keys and old["status"] in ("pending", "attention") and old["actual_amount_cents"] is None:
                db.execute("DELETE FROM plan_items WHERE id = ?", (old["id"],))
    return plan_id


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
    automatic_plan_id = _ensure_automatic_checklist()
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
                      weekly_target_cents, target_date, priority
               FROM savings_goals WHERE active = 1 ORDER BY priority, name COLLATE NOCASE"""
        ).fetchall():
            target = row["target_amount_cents"]
            goals.append({
                "id": row["id"], "name": row["name"], "target_amount_cents": target,
                "current_balance_cents": row["current_balance_cents"],
                "weekly_target_cents": row["weekly_target_cents"],
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
            """SELECT COUNT(*) FROM plan_items
               WHERE plan_id = ? AND status IN ('pending', 'attention')""",
            (automatic_plan_id,),
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
    plan_id = _ensure_automatic_checklist()
    with database.connect() as db:
        rows = db.execute(
            """SELECT pi.id, pi.plan_id, pp.period_start AS plan_period_start,
                      pp.period_end AS plan_period_end, pi.category, pi.name,
                      pi.requested_cents, pi.funded_cents, pi.unfunded_cents,
                      pi.due_date, pi.note, pi.status, pi.actual_amount_cents,
                      pi.actual_date
               FROM plan_items pi JOIN payday_plans pp ON pp.id = pi.plan_id
               WHERE pi.plan_id = ? AND pi.status IN ('pending', 'attention')
               ORDER BY COALESCE(pi.due_date, pp.period_start), pi.id""",
            (plan_id,),
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
            existing = db.execute(
                """SELECT pi.*, pp.period_start, pp.period_end
                   FROM plan_items pi JOIN payday_plans pp ON pp.id = pi.plan_id
                   WHERE pi.id = ?""", (item_id,)
            ).fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Checklist item not found")

            # Savings entries are real contributions, not just checklist notes.
            # Create/update a transaction and a recorded allocation so the goal
            # balance and weekly carryover history remain consistent on refresh.
            if existing["category"] == "savings" and existing["savings_goal_id"] is not None and "actual_amount_cents" in updates:
                new_amount = updates["actual_amount_cents"]
                old_amount = existing["actual_amount_cents"] or 0
                goal_id = existing["savings_goal_id"]
                transaction_id = existing["actual_transaction_id"]
                transaction_date = updates.get("actual_date") or existing["actual_date"] or date.today().isoformat()
                if new_amount is None or new_amount == 0:
                    if transaction_id:
                        db.execute("DELETE FROM surplus_allocations WHERE transaction_id = ?", (transaction_id,))
                        db.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
                    if old_amount:
                        db.execute(
                            "UPDATE savings_goals SET current_balance_cents = MAX(0, current_balance_cents - ?) WHERE id = ?",
                            (old_amount, goal_id),
                        )
                    transaction_id = None
                    updates["actual_transaction_id"] = None
                    updates["actual_date"] = None if new_amount is None else transaction_date
                else:
                    goal = db.execute(
                        "SELECT name FROM savings_goals WHERE id = ?", (goal_id,)
                    ).fetchone()
                    if goal is None:
                        raise HTTPException(status_code=422, detail="Linked savings goal no longer exists")
                    if transaction_id:
                        db.execute(
                            """UPDATE transactions SET transaction_date = ?, category = 'savings contribution',
                               transaction_type = 'expense', description = ?, amount_cents = ?
                               WHERE id = ?""",
                            (transaction_date, "Contribution to " + goal["name"], new_amount, transaction_id),
                        )
                        db.execute(
                            """UPDATE surplus_allocations SET allocation_date = ?, amount_cents = ?,
                               actual_amount_cents = ?, status = 'recorded' WHERE transaction_id = ?""",
                            (transaction_date, max(new_amount, existing["requested_cents"], 1), new_amount, transaction_id),
                        )
                    else:
                        cursor = db.execute(
                            """INSERT INTO transactions
                               (transaction_date, category, transaction_type, description, amount_cents, notes)
                               VALUES (?, 'savings contribution', 'expense', ?, ?, ?)""",
                            (transaction_date, "Contribution to " + goal["name"], new_amount,
                             "Recorded from automatic weekly checklist item #" + str(item_id)),
                        )
                        transaction_id = cursor.lastrowid
                        db.execute(
                            """INSERT INTO surplus_allocations
                               (allocation_date, allocation_type, amount_cents, savings_goal_id,
                                description, status, actual_amount_cents, transaction_id)
                               VALUES (?, 'savings', ?, ?, ?, 'recorded', ?, ?)""",
                            (transaction_date, max(new_amount, existing["requested_cents"], 1),
                             goal_id, "Checklist savings contribution", new_amount, transaction_id),
                        )
                    delta = new_amount - old_amount
                    if delta:
                        db.execute(
                            "UPDATE savings_goals SET current_balance_cents = MAX(0, current_balance_cents + ?) WHERE id = ?",
                            (delta, goal_id),
                        )
                    updates["actual_transaction_id"] = transaction_id
                    updates["actual_date"] = transaction_date

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
<p class="muted">This checklist is generated automatically from your current financial week, saved schedules, savings goals, and safe cash-flow forecast. Record what you actually did; this never sends a payment. For paycheck and bill reconciliation, also record the transaction.</p>
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
function renderChecklistPage(){const root=document.getElementById("items");const nav=document.getElementById("checklist-pagination");root.replaceChildren();nav.replaceChildren();if(!checklistItems.length){root.appendChild(el("p","muted","Nothing needs attention right now for this financial week."));return;}
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

"""API and browser management page for bills and debts."""
import sqlite3
from datetime import date

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse

from . import database
from .obligation_schemas import (
    BillCreate, BillOut, BillUpdate, DebtCreate, DebtOut, DebtUpdate,
)

router = APIRouter()


def _bill_out(row) -> dict:
    item = dict(row)
    item["autopay"] = bool(item["autopay"])
    item["active"] = bool(item["active"])
    return item


def _debt_out(row) -> dict:
    item = dict(row)
    item["active"] = bool(item["active"])
    return item


def _clean_name(updates: dict) -> dict:
    if "name" in updates:
        updates["name"] = updates["name"].strip()
        if not updates["name"]:
            raise HTTPException(status_code=422, detail="name cannot be blank")
    if "category" in updates:
        updates["category"] = updates["category"].strip().lower()
        if not updates["category"]:
            raise HTTPException(status_code=422, detail="category cannot be blank")
    return updates


@router.get("/api/bills", response_model=list[BillOut])
def list_bills():
    with database.connect() as db:
        rows = db.execute(
            """SELECT id, name, amount_cents, due_day_of_month, next_due_date,
                      flexible_window_days, category, frequency, autopay, active, created_at
               FROM bills ORDER BY COALESCE(next_due_date, '9999-12-31'), id"""
        ).fetchall()
    return [_bill_out(row) for row in rows]


@router.post("/api/bills", response_model=BillOut, status_code=status.HTTP_201_CREATED)
def create_bill(body: BillCreate):
    values = body.model_dump()
    values = _clean_name(values)
    values["next_due_date"] = body.next_due_date.isoformat()
    values["autopay"] = int(body.autopay)
    values["active"] = int(body.active)
    try:
        with database.connect() as db:
            cursor = db.execute(
                """INSERT INTO bills
                   (name, amount_cents, due_day_of_month, next_due_date,
                    flexible_window_days, category, frequency, autopay, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (values["name"], values["amount_cents"], values["due_day_of_month"],
                 values["next_due_date"], values["flexible_window_days"], values["category"],
                 values["frequency"], values["autopay"], values["active"]),
            )
            row = db.execute(
                """SELECT id, name, amount_cents, due_day_of_month, next_due_date,
                          flexible_window_days, category, frequency, autopay, active, created_at
                   FROM bills WHERE id = ?""", (cursor.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Bill violates a database constraint") from exc
    return _bill_out(row)


@router.patch("/api/bills/{bill_id}", response_model=BillOut)
def update_bill(bill_id: int, body: BillUpdate):
    updates = _clean_name(body.model_dump(exclude_unset=True))
    if not updates:
        raise HTTPException(status_code=422, detail="no fields to update")
    if "next_due_date" in updates:
        updates["next_due_date"] = updates["next_due_date"].isoformat()
    for key in ("active", "autopay"):
        if key in updates:
            updates[key] = int(updates[key])
    allowed = {"name", "amount_cents", "due_day_of_month", "next_due_date",
               "flexible_window_days", "category", "frequency", "autopay", "active"}
    if not set(updates) <= allowed:
        raise HTTPException(status_code=422, detail="unsupported field")
    try:
        with database.connect() as db:
            if db.execute("SELECT id FROM bills WHERE id = ?", (bill_id,)).fetchone() is None:
                raise HTTPException(status_code=404, detail="Bill not found")
            assignments = ", ".join(f"{key} = ?" for key in updates)
            db.execute(f"UPDATE bills SET {assignments} WHERE id = ?", (*updates.values(), bill_id))
            row = db.execute(
                """SELECT id, name, amount_cents, due_day_of_month, next_due_date,
                          flexible_window_days, category, frequency, autopay, active, created_at
                   FROM bills WHERE id = ?""", (bill_id,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Bill update violates a database constraint") from exc
    return _bill_out(row)


@router.delete("/api/bills/{bill_id}")
def delete_bill(bill_id: int):
    with database.connect() as db:
        cursor = db.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Bill not found")
    return {"deleted": True, "id": bill_id}


@router.get("/api/debts", response_model=list[DebtOut])
def list_debts():
    with database.connect() as db:
        rows = db.execute(
            """SELECT id, name, balance_cents, minimum_payment_cents, apr_percent,
                      due_day_of_month, next_due_date, active, created_at
               FROM credit_cards ORDER BY COALESCE(next_due_date, '9999-12-31'), id"""
        ).fetchall()
    return [_debt_out(row) for row in rows]


@router.post("/api/debts", response_model=DebtOut, status_code=status.HTTP_201_CREATED)
def create_debt(body: DebtCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name cannot be blank")
    due_day = body.due_day_of_month or body.next_due_date.day
    try:
        with database.connect() as db:
            cursor = db.execute(
                """INSERT INTO credit_cards
                   (name, balance_cents, minimum_payment_cents, due_day_of_month,
                    apr_percent, active, next_due_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, body.balance_cents, body.minimum_payment_cents, due_day,
                 body.apr_percent, int(body.active), body.next_due_date.isoformat()),
            )
            row = db.execute(
                """SELECT id, name, balance_cents, minimum_payment_cents, apr_percent,
                          due_day_of_month, next_due_date, active, created_at
                   FROM credit_cards WHERE id = ?""", (cursor.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Debt violates a database constraint") from exc
    return _debt_out(row)


@router.patch("/api/debts/{debt_id}", response_model=DebtOut)
def update_debt(debt_id: int, body: DebtUpdate):
    updates = body.model_dump(exclude_unset=True)
    if "name" in updates:
        updates["name"] = updates["name"].strip()
        if not updates["name"]:
            raise HTTPException(status_code=422, detail="name cannot be blank")
    if "next_due_date" in updates:
        updates["next_due_date"] = updates["next_due_date"].isoformat()
    if "active" in updates:
        updates["active"] = int(updates["active"])
    if not updates:
        raise HTTPException(status_code=422, detail="no fields to update")
    allowed = {"name", "balance_cents", "minimum_payment_cents", "apr_percent",
               "next_due_date", "due_day_of_month", "active"}
    if not set(updates) <= allowed:
        raise HTTPException(status_code=422, detail="unsupported field")
    try:
        with database.connect() as db:
            if db.execute("SELECT id FROM credit_cards WHERE id = ?", (debt_id,)).fetchone() is None:
                raise HTTPException(status_code=404, detail="Debt not found")
            assignments = ", ".join(f"{key} = ?" for key in updates)
            db.execute(f"UPDATE credit_cards SET {assignments} WHERE id = ?", (*updates.values(), debt_id))
            row = db.execute(
                """SELECT id, name, balance_cents, minimum_payment_cents, apr_percent,
                          due_day_of_month, next_due_date, active, created_at
                   FROM credit_cards WHERE id = ?""", (debt_id,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Debt update violates a database constraint") from exc
    return _debt_out(row)


@router.delete("/api/debts/{debt_id}")
def delete_debt(debt_id: int):
    with database.connect() as db:
        cursor = db.execute("DELETE FROM credit_cards WHERE id = ?", (debt_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Debt not found")
    return {"deleted": True, "id": debt_id}


@router.get("/bills", response_class=HTMLResponse)
def bills_page():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bills & Debt · CashFlow Engine</title>
<style>
:root{font-family:system-ui,sans-serif;color:#172033;background:#f3f6fa}*{box-sizing:border-box}
body{margin:0;padding:24px 14px 48px}main{max-width:1100px;margin:auto}a{color:#0e7490;font-weight:650;text-decoration:none}
h1{color:#155e75;margin:8px 0}h2{font-size:1.2rem}.muted{color:#5c687a;line-height:1.5}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr));gap:18px}
.card{background:white;padding:22px;border-radius:16px;box-shadow:0 8px 28px #172b4d0d;min-width:0}
label{display:block;font-weight:650;font-size:.9rem;margin:12px 0 5px}
input,select{width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font:inherit;background:white}
button{border:0;border-radius:8px;padding:9px 12px;margin-top:12px;background:#0e7490;color:white;font-weight:700;cursor:pointer}
button:disabled{opacity:.55}.toggle{background:#475569;margin:4px 0}.status{padding:12px;border-radius:9px;background:#e0f2fe;color:#075985}.error{background:#fee2e2;color:#991b1b}
.status[hidden]{display:none}.list{padding:0;list-style:none}.list li{padding:12px 0;border-top:1px solid #e2e8f0;overflow-wrap:anywhere}
.title{font-weight:700}.detail{color:#5c687a;font-size:.9rem;margin-top:4px}
</style></head><body><main>
<a href="/">← Dashboard</a><h1>Bills & Debt</h1>
<p class="muted">Track recurring bills and credit-card or loan balances. Money is entered as dollars here and stored as integer cents. Saving a record does not pay it.</p>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<div class="grid">
<section class="card"><h2>Recurring bills</h2>
<form id="bill-form">
<label for="bill-name">Bill name</label><input id="bill-name" name="name" maxlength="120" required placeholder="Rent, electricity, internet">
<label for="bill-amount">Expected amount ($)</label><input id="bill-amount" name="amount" type="number" min="0" step="0.01" required>
<label for="bill-date">Next due date</label><input id="bill-date" name="next_due_date" type="date" required>
<label for="bill-frequency">Recurrence</label><select id="bill-frequency" name="frequency">
<option value="weekly">Weekly</option><option value="biweekly">Every two weeks</option><option value="semimonthly">1st and 15th</option><option value="twice_monthly_last">15th and last calendar day</option><option value="first_third_friday">1st and 3rd Friday</option><option value="last_business_day">Last business day</option><option value="monthly" selected>Monthly</option><option value="quarterly">Quarterly</option><option value="yearly">Yearly</option><option value="irregular">One-time / irregular</option></select>
<label for="bill-category">Category</label><select id="bill-category" name="category">
<option value="housing">Housing</option><option value="utilities">Utilities</option><option value="transportation">Transportation</option><option value="insurance">Insurance</option><option value="subscriptions">Subscriptions</option><option value="food">Food</option><option value="other" selected>Other</option></select>
<label for="bill-window">Flexible window (days)</label><input id="bill-window" name="flexible_window_days" type="number" min="0" max="31" step="1" value="0" required>
<label><input name="autopay" type="checkbox" style="width:auto"> Paid by autopay</label>
<button type="submit">Save bill</button></form>
<ul id="bills" class="list"><li>Loading bills…</li></ul></section>
<section class="card"><h2>Credit cards & debts</h2>
<form id="debt-form">
<label for="debt-name">Account name</label><input id="debt-name" name="name" maxlength="120" required placeholder="Credit card, auto loan">
<label for="debt-balance">Current balance ($)</label><input id="debt-balance" name="balance" type="number" min="0" step="0.01" required>
<label for="debt-minimum">Minimum payment ($)</label><input id="debt-minimum" name="minimum" type="number" min="0" step="0.01" required>
<label for="debt-apr">APR (%)</label><input id="debt-apr" name="apr_percent" type="number" min="0" max="1000" step="0.01" required>
<label for="debt-date">Next due date</label><input id="debt-date" name="next_due_date" type="date" required>
<button type="submit">Save debt</button></form>
<ul id="debts" class="list"><li>Loading debts…</li></ul></section>
</div></main>
<script>
const box=document.getElementById("status");
function message(text,error=false){box.textContent=text;box.classList.toggle("error",error);box.hidden=false;}
function cents(value){const n=Number(value);if(!Number.isFinite(n)||n<0)throw Error("Enter a valid non-negative amount.");return Math.round((n+Number.EPSILON)*100);}
function dollars(value){return new Intl.NumberFormat(undefined,{style:"currency",currency:"USD"}).format(value/100);}
async function api(url,options={}){const r=await fetch(url,{headers:{"Content-Type":"application/json"},...options});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"Please check your entries.");return d;}
function el(tag,cls,txt){const n=document.createElement(tag);if(cls)n.className=cls;n.textContent=txt;return n;}
function addToggle(item,endpoint,active,label){const b=el("button","toggle",active?"Deactivate":"Reactivate");b.type="button";b.addEventListener("click",async()=>{b.disabled=true;try{await api(endpoint,{method:"PATCH",body:JSON.stringify({active:!active})});await refresh();message(label+" status updated.");}catch(e){message(e.message,true);b.disabled=false;}});item.appendChild(b);}
function render(list,records,kind){list.replaceChildren();if(!records.length){list.appendChild(el("li","muted","No "+kind+" saved yet."));return;}
for(const r of records){const li=el("li");li.appendChild(el("div","title",r.name+" · "+dollars(kind==="bills"?r.amount_cents:r.balance_cents)));
const detail=kind==="bills"?r.category+" · "+r.frequency+" · due "+(r.next_due_date||"not set")+(r.autopay?" · autopay":""):
"Minimum "+dollars(r.minimum_payment_cents)+" · "+r.apr_percent+"% APR · due "+(r.next_due_date||"not set");
li.appendChild(el("div","detail",detail+(r.active?"":" · inactive")));
const edit=el("button","toggle",kind==="bills"?"Edit bill":"Edit debt");edit.type="button";
edit.addEventListener("click",async()=>{
try{
if(kind==="bills"){
const name=prompt("Bill name:",r.name);if(name===null)return;
const amount=prompt("Amount in dollars:",(r.amount_cents/100).toFixed(2));if(amount===null)return;
const due=prompt("Next due date YYYY-MM-DD:",r.next_due_date||"");if(due===null)return;
const frequency=prompt("Frequency (weekly, biweekly, monthly, quarterly, yearly, irregular):",r.frequency);if(frequency===null)return;
const category=prompt("Category:",r.category);if(category===null)return;
const window=prompt("Flexible window in days:",String(r.flexible_window_days));if(window===null)return;
const autopayText=prompt("Autopay true or false:",String(r.autopay));if(autopayText===null)return;
if(!["true","false"].includes(autopayText.trim().toLowerCase()))throw Error("Autopay must be true or false.");
await api("/api/bills/"+r.id,{method:"PATCH",body:JSON.stringify({name:name.trim(),amount_cents:cents(amount),next_due_date:due,frequency:frequency.trim(),category:category.trim(),flexible_window_days:Number(window),autopay:autopayText.trim().toLowerCase()==="true"})});
}else{
const name=prompt("Debt name:",r.name);if(name===null)return;
const balance=prompt("Current balance in dollars:",(r.balance_cents/100).toFixed(2));if(balance===null)return;
const minimum=prompt("Minimum payment in dollars:",(r.minimum_payment_cents/100).toFixed(2));if(minimum===null)return;
const apr=prompt("APR percent:",String(r.apr_percent));if(apr===null)return;
const due=prompt("Next due date YYYY-MM-DD:",r.next_due_date||"");if(due===null)return;
await api("/api/debts/"+r.id,{method:"PATCH",body:JSON.stringify({name:name.trim(),balance_cents:cents(balance),minimum_payment_cents:cents(minimum),apr_percent:Number(apr),next_due_date:due})});
}
await refresh();message((kind==="bills"?"Bill":"Debt")+" updated.");
}catch(e){message(e.message,true);}
});li.appendChild(edit);
addToggle(li,"/api/"+(kind==="bills"?"bills":"debts")+"/"+r.id,r.active,kind==="bills"?"Bill":"Debt");
const remove=el("button","toggle","Delete "+(kind==="bills"?"bill":"debt"));remove.type="button";remove.addEventListener("click",async()=>{if(!confirm("Permanently delete "+r.name+"?"))return;remove.disabled=true;try{await api("/api/"+(kind==="bills"?"bills":"debts")+"/"+r.id,{method:"DELETE"});await refresh();message((kind==="bills"?"Bill":"Debt")+" deleted.");}catch(e){message(e.message,true);remove.disabled=false;}});li.appendChild(remove);list.appendChild(li);}}
async function refresh(){const [b,d]=await Promise.all([api("/api/bills"),api("/api/debts")]);render(document.getElementById("bills"),b,"bills");render(document.getElementById("debts"),d,"debts");}
function bindForm(id,url,convert,success){const form=document.getElementById(id);form.addEventListener("submit",async(e)=>{e.preventDefault();const button=form.querySelector("button");button.disabled=true;try{await api(url,{method:"POST",body:JSON.stringify(convert(form))});form.reset();await refresh();message(success);}catch(err){message(err.message,true);}finally{button.disabled=false;}});}
bindForm("bill-form","/api/bills",f=>({name:f.elements.name.value.trim(),amount_cents:cents(f.elements.amount.value),next_due_date:f.elements.next_due_date.value,frequency:f.elements.frequency.value,category:f.elements.category.value,flexible_window_days:Number(f.elements.flexible_window_days.value),autopay:f.elements.autopay.checked}),"Bill saved.");
bindForm("debt-form","/api/debts",f=>({name:f.elements.name.value.trim(),balance_cents:cents(f.elements.balance.value),minimum_payment_cents:cents(f.elements.minimum.value),apr_percent:Number(f.elements.apr_percent.value),next_due_date:f.elements.next_due_date.value}),"Debt saved.");
refresh().catch(e=>message("Could not load saved records: "+e.message,true));
</script></body></html>"""

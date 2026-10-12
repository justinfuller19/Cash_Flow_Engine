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
    IncomeSourceCreate, IncomeSourceOut, IncomeSourceUpdate,
    PersonCreate, PersonOut, PersonUpdate,
)
from .schemas import PaydayPlanOut, PaydayPreviewRequest
from .obligations import router as obligations_router
from .savings import router as savings_router
from .planning import router as planning_router
from .dashboard import router as dashboard_router
from .transactions import router as transactions_router
from .security import remote_access_guard


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database.initialize_database()
    yield


app = FastAPI(
    title="CashFlow Engine",
    description="Self-hosted household cash-flow planning with SQLite-backed household setup.",
    version="0.8.0",
    lifespan=lifespan,
)

app.include_router(obligations_router)
app.include_router(savings_router)
app.include_router(planning_router)
app.include_router(dashboard_router)
app.include_router(transactions_router)
app.middleware("http")(remote_access_guard)


@app.get("/health")
def health():
    return {"status": "ok", "app": "CashFlow Engine"}


@app.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CashFlow Engine</title><style>
:root{font-family:system-ui,sans-serif;color:#18212f;background:#f3f6fa}*{box-sizing:border-box}
body{margin:0;padding:24px 14px 48px}main{max-width:1100px;margin:auto}
h1{margin:0 0 8px;color:#155e75}h2{font-size:1.12rem;margin:0 0 12px}
p{line-height:1.5}.muted{color:#5b6678}a{color:#0e7490;font-weight:650;text-decoration:none}
nav{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0}
nav a{background:white;padding:10px 13px;border-radius:9px;box-shadow:0 3px 14px #172b4d0d}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:16px}
.card{background:white;padding:20px;border-radius:15px;box-shadow:0 8px 28px #172b4d0d;min-width:0}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}
.metric{background:white;padding:14px;border-radius:12px}.metric span{display:block;color:#5b6678;font-size:.9rem}
.metric strong{display:block;font-size:1.25rem;color:#155e75;margin-top:4px}
.list{list-style:none;padding:0;margin:0}.list li{padding:10px 0;border-top:1px solid #e5eaf0;overflow-wrap:anywhere}
.list li:first-child{border-top:0}.item-title{font-weight:700}.detail{font-size:.9rem;color:#5b6678;margin-top:3px}
.status{padding:12px;border-radius:9px;background:#e0f2fe;color:#075985}.error{background:#fee2e2;color:#991b1b}.status[hidden]{display:none}
.progress{height:7px;background:#e2e8f0;border-radius:7px;overflow:hidden;margin-top:7px}.fill{height:100%;background:#0e7490}
</style></head><body><main>
<h1>CashFlow Engine</h1><p class="muted">Your household cash-flow overview. Forecasts help you plan; they do not send payments or move money.</p>
<nav><a href="/setup">Household & Income</a><a href="/bills">Bills & Debt</a><a href="/savings">Savings Goals</a><a href="/planner">Payday Planner</a><a href="/checklist">Payday Checklist</a><a href="/transactions">Transactions</a><a href="/docs">API Docs</a></nav>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<div id="metrics" class="metrics"></div>
<div class="grid">
<section class="card"><h2>Next paydays</h2><ul id="paydays" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Upcoming bills · 30 days</h2><ul id="bills" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Debt payments · 30 days</h2><ul id="debts" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Savings progress</h2><ul id="goals" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Recent payday plans</h2><ul id="plans" class="list"><li class="muted">Loading…</li></ul><p><a href="/planner">Generate or review plans →</a></p></section>
<section class="card"><h2>Checklist</h2><p id="checklist-summary" class="muted">Loading checklist…</p><a href="/checklist">Review payday actions →</a></section>
<section class="card"><h2>Recent transactions</h2><ul id="transactions" class="list"><li class="muted">Loading…</li></ul><a href="/transactions">Record or review transactions →</a></section>
</div></main>
<script>
const statusBox=document.getElementById("status");
function el(tag,cls,txt){const n=document.createElement(tag);if(cls)n.className=cls;n.textContent=txt;return n;}
function dollars(cents){return new Intl.NumberFormat(undefined,{style:"currency",currency:"USD"}).format(cents/100);}
async function api(url){const r=await fetch(url);const d=await r.json().catch(()=>({}));if(!r.ok)throw Error("Could not load dashboard data.");return d;}
function renderList(id,items,empty,make){const list=document.getElementById(id);list.replaceChildren();if(!items.length){list.appendChild(el("li","muted",empty));return;}for(const item of items){const li=el("li","");make(li,item);list.appendChild(li);}}
async function load(){const d=await api("/api/dashboard");
const metrics=document.getElementById("metrics");metrics.replaceChildren();
for(const [label,value] of [["Active household members",String(d.active_household_members)],["Protected buffers",dollars(d.protected_buffers_cents)],["Actions needing attention",String(d.pending_checklist_count)],["Recorded income this month",dollars(d.month_income_cents)],["Recorded expenses this month",dollars(d.month_expenses_cents)]]){const card=el("div","metric","");card.appendChild(el("span","",label));card.appendChild(el("strong","",value));metrics.appendChild(card);}
renderList("paydays",d.next_paydays,"No upcoming paydays found.",(li,x)=>{li.appendChild(el("div","item-title",x.name+" · "+dollars(x.amount_cents)));li.appendChild(el("div","detail",x.date));});
renderList("bills",d.upcoming_bills,"No saved bills due in the next 30 days.",(li,x)=>{li.appendChild(el("div","item-title",x.name+" · "+dollars(x.amount_cents)));li.appendChild(el("div","detail",x.date+" · "+x.category+(x.autopay?" · autopay":"")));});
renderList("debts",d.upcoming_debts,"No debt due dates found in the next 30 days.",(li,x)=>{li.appendChild(el("div","item-title",x.name+" · minimum "+dollars(x.minimum_payment_cents)));li.appendChild(el("div","detail",x.date+" · balance "+dollars(x.balance_cents)));});
renderList("goals",d.savings_goals,"No active savings goals.",(li,x)=>{li.appendChild(el("div","item-title",x.name+" · "+dollars(x.current_balance_cents)+" / "+dollars(x.target_amount_cents)));li.appendChild(el("div","detail",x.progress_percent+"% saved · "+dollars(x.contribution_per_period_cents)+" per payday"));const track=el("div","progress","");const fill=el("div","fill","");fill.style.width=x.progress_percent+"%";track.appendChild(fill);li.appendChild(track);});
renderList("plans",d.recent_plans,"No saved payday plans yet.",(li,x)=>{li.appendChild(el("div","item-title","Plan "+x.id+" · "+x.period_start+" to "+x.period_end));li.appendChild(el("div","detail",dollars(x.allocated_cents)+" allocated · "+dollars(x.unfunded_cents)+" unfunded"));const a=el("a","","View plan");a.href="/planner";li.appendChild(a);});
document.getElementById("checklist-summary").textContent=d.pending_checklist_count?d.pending_checklist_count+" pending or attention-needed action(s).":"No pending checklist actions.";
renderList("transactions",d.recent_transactions,"No transactions recorded yet.",(li,x)=>{const sign=x.amount_cents<0?"−":"";li.appendChild(el("div","item-title",x.description+" · "+sign+dollars(Math.abs(x.amount_cents))));li.appendChild(el("div","detail",x.transaction_date+" · "+x.category+(x.account_name?" · "+x.account_name:"")));});
}
load().catch(e=>{statusBox.textContent=e.message;statusBox.classList.add("error");statusBox.hidden=false;});
</script></body></html>"""


@app.get("/setup", response_class=HTMLResponse)
def household_setup():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Household Setup · CashFlow Engine</title>
<style>
:root{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#172033;background:#f3f6fa;font-synthesis:none}
*{box-sizing:border-box}body{margin:0;padding:24px 16px 48px}main{max-width:1050px;margin:0 auto}
header{margin:0 0 24px}h1{margin:0;color:#155e75;font-size:clamp(1.8rem,4vw,2.4rem)}h2{margin-top:0;font-size:1.2rem}
p{line-height:1.5}.muted{color:#5c687a}.back{color:#0e7490;font-weight:650;text-decoration:none;display:inline-block;margin-bottom:12px}
.layout{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr));gap:18px}
.card{background:white;border-radius:16px;padding:22px;box-shadow:0 8px 28px #172b4d0d;min-width:0}
label{display:block;font-size:.92rem;font-weight:650;margin:13px 0 6px}input,select{width:100%;padding:11px 12px;border:1px solid #cbd5e1;border-radius:9px;font:inherit;background:white;color:inherit}
button{margin-top:16px;border:0;border-radius:9px;background:#0e7490;color:white;padding:11px 16px;font:inherit;font-weight:700;cursor:pointer}
button:hover{background:#155e75}button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid #67e8f9;outline-offset:2px}
.status{margin:0 0 18px;padding:12px 14px;border-radius:10px;background:#e0f2fe;color:#075985}.status.error{background:#fee2e2;color:#991b1b}.status[hidden]{display:none}
.list{list-style:none;padding:0;margin:14px 0 0}.list li{border-top:1px solid #e5eaf0;padding:12px 0;overflow-wrap:anywhere}.list li:first-child{border-top:0}
.item-title{font-weight:700}.item-detail{font-size:.9rem;color:#5c687a;margin-top:3px}.empty{color:#64748b;font-style:italic}.small-button{margin:8px 8px 0 0;padding:7px 10px;font-size:.88rem}
@media(max-width:480px){body{padding:16px 10px 32px}.card{padding:17px}}
</style></head><body><main>
<header><a class="back" href="/">← Dashboard</a><h1>Household Setup</h1>
<p class="muted">Start by adding the people in your household and their take-home pay. Amounts are saved as cents in your local SQLite database.</p></header>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<div class="layout">
<section class="card"><h2>Household members</h2><p class="muted">Set the personal cash buffer you want protected before other allocations.</p>
<form id="person-form">
<label for="person-name">Name</label><input id="person-name" name="name" maxlength="120" autocomplete="name" required>
<label for="person-buffer">Default cash buffer ($)</label><input id="person-buffer" name="buffer" type="number" min="0" step="0.01" value="0.00" inputmode="decimal" required>
<button type="submit">Add household member</button></form>
<ul id="people-list" class="list" aria-label="Saved household members"><li class="empty">Loading members…</li></ul>
</section>
<section class="card"><h2>Income sources</h2><p class="muted">Record net take-home pay and the next expected payday.</p>
<form id="income-form">
<label for="income-person">Household member</label><select id="income-person" name="person_id" required><option value="">Add a member first</option></select>
<label for="income-name">Income source</label><input id="income-name" name="name" maxlength="120" placeholder="e.g. Main paycheck" required>
<label for="income-amount">Net amount per paycheck ($)</label><input id="income-amount" name="amount" type="number" min="0" step="0.01" inputmode="decimal" required>
<label for="income-frequency">Pay frequency</label><select id="income-frequency" name="frequency" required>
<option value="weekly">Weekly</option><option value="biweekly" selected>Every two weeks</option><option value="semimonthly">Twice a month</option><option value="monthly">Monthly</option><option value="irregular">Irregular</option></select>
<label for="income-next">Next payday</label><input id="income-next" name="next_payday" type="date" required>
<label><input id="income-active" name="active" type="checkbox" checked style="width:auto;margin-right:8px">This income is active</label>
<button type="submit">Add income source</button></form>
<ul id="income-list" class="list" aria-label="Saved income sources"><li class="empty">Loading income sources…</li></ul>
</section></div>
<p class="muted">Continue with Bills & Debt, Savings Goals, Payday Planner, or Checklist from the dashboard. This screen does not connect to your bank or move money.</p>
</main>
<script>
const statusBox = document.getElementById("status");
const peopleList = document.getElementById("people-list");
const incomeList = document.getElementById("income-list");
const personSelect = document.getElementById("income-person");
let people = [];
function showStatus(message, isError = false) {
  statusBox.textContent = message;
  statusBox.classList.toggle("error", isError);
  statusBox.hidden = false;
}
function dollars(cents) {
  return new Intl.NumberFormat(undefined, {style:"currency", currency:"USD"}).format(cents / 100);
}
function centsFromInput(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount < 0) throw new Error("Enter a valid non-negative dollar amount.");
  return Math.round((amount + Number.EPSILON) * 100);
}
async function api(url, options = {}) {
  const response = await fetch(url, {headers:{"Content-Type":"application/json"}, ...options});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "Please check the form and try again.";
    throw new Error(detail);
  }
  return data;
}
function addText(parent, tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = value;
  parent.appendChild(element);
  return element;
}
function emptyList(list, message) {
  list.replaceChildren();
  addText(list, "li", "empty", message);
}
function addSmallButton(parent, label, onClick, secondary = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "small-button";
  if (secondary) button.style.background = "#475569";
  button.textContent = label;
  button.addEventListener("click", onClick);
  parent.appendChild(button);
  return button;
}
function renderPeople() {
  peopleList.replaceChildren();
  personSelect.replaceChildren();
  const activePeople = people.filter((person) => person.active);
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = activePeople.length ? "Choose a member" : "Add an active member first";
  personSelect.appendChild(placeholder);
  for (const person of people) {
    const item = document.createElement("li");
    addText(item, "div", "item-title", person.name + (person.active ? "" : " · inactive"));
    addText(item, "div", "item-detail", "Protected buffer: " + dollars(person.default_buffer_cents));
    addSmallButton(item, "Edit member", async () => {
      const name = prompt("Household member name:", person.name);
      if (name === null) return;
      const buffer = prompt("Protected buffer in dollars:", (person.default_buffer_cents / 100).toFixed(2));
      if (buffer === null) return;
      try {
        await api("/api/people/" + person.id, {method:"PATCH", body:JSON.stringify({
          name:name.trim(), default_buffer_cents:centsFromInput(buffer)
        })});
        await refresh(); showStatus("Household member updated.");
      } catch (error) { showStatus(error.message, true); }
    }, true);
    addSmallButton(item, person.active ? "Deactivate member" : "Reactivate member", async () => {
      try {
        await api("/api/people/" + person.id, {method:"PATCH", body:JSON.stringify({active:!person.active})});
        await refresh(); showStatus("Household member status updated.");
      } catch (error) { showStatus(error.message, true); }
    }, true);
    addSmallButton(item, "Delete member", async () => {
      if (!confirm("Permanently delete " + person.name + "? This also deletes their income sources. Saved plans and transactions remain.")) return;
      try {
        await api("/api/people/" + person.id, {method:"DELETE"});
        await refresh(); showStatus("Household member deleted.");
      } catch (error) { showStatus(error.message, true); }
    }, true);
    peopleList.appendChild(item);
    if (person.active) {
      const option = document.createElement("option");
      option.value = String(person.id);
      option.textContent = person.name;
      personSelect.appendChild(option);
    }
  }
  if (!people.length) emptyList(peopleList, "No household members yet.");
}
function renderIncome(incomes) {
  incomeList.replaceChildren();
  for (const income of incomes) {
    const person = people.find((item) => item.id === income.person_id);
    const item = document.createElement("li");
    addText(item, "div", "item-title", income.name + " · " + dollars(income.net_amount_cents));
    addText(item, "div", "item-detail", (person ? person.name : "Unknown member") + " · " +
      income.frequency + " · next payday " + income.next_payday + (income.active ? "" : " · inactive"));
    addSmallButton(item, "Edit income", async () => {
      const name = prompt("Income source name:", income.name);
      if (name === null) return;
      const amount = prompt("Net amount per paycheck in dollars:", (income.net_amount_cents / 100).toFixed(2));
      if (amount === null) return;
      const frequency = prompt("Frequency (weekly, biweekly, semimonthly, monthly, irregular):", income.frequency);
      if (frequency === null) return;
      const nextPayday = prompt("Next payday YYYY-MM-DD:", income.next_payday);
      if (nextPayday === null) return;
      try {
        await api("/api/income-sources/" + income.id, {method:"PATCH", body:JSON.stringify({
          name:name.trim(), net_amount_cents:centsFromInput(amount),
          frequency:frequency.trim(), next_payday:nextPayday
        })});
        await refresh(); showStatus("Income source updated.");
      } catch (error) { showStatus(error.message, true); }
    }, true);
    addSmallButton(item, income.active ? "Deactivate income" : "Reactivate income", async () => {
      try {
        await api("/api/income-sources/" + income.id, {method:"PATCH", body:JSON.stringify({active:!income.active})});
        await refresh(); showStatus("Income source status updated.");
      } catch (error) { showStatus(error.message, true); }
    }, true);
    addSmallButton(item, "Delete income", async () => {
      if (!confirm("Permanently delete income source " + income.name + "?")) return;
      try {
        await api("/api/income-sources/" + income.id, {method:"DELETE"});
        await refresh(); showStatus("Income source deleted.");
      } catch (error) { showStatus(error.message, true); }
    }, true);
    incomeList.appendChild(item);
  }
  if (!incomes.length) emptyList(incomeList, "No income sources yet.");
}
async function refresh() {
  [people] = await Promise.all([api("/api/people")]);
  renderPeople();
  const incomes = await api("/api/income-sources");
  renderIncome(incomes);
}
document.getElementById("person-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    await api("/api/people", {method:"POST", body:JSON.stringify({
      name:form.elements.name.value.trim(),
      default_buffer_cents:centsFromInput(form.elements.buffer.value)
    })});
    form.reset();
    form.elements.buffer.value = "0.00";
    await refresh();
    showStatus("Household member saved.");
  } catch (error) { showStatus(error.message, true); }
  finally { button.disabled = false; }
});
document.getElementById("income-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    await api("/api/income-sources", {method:"POST", body:JSON.stringify({
      person_id:Number(form.elements.person_id.value),
      name:form.elements.name.value.trim(),
      net_amount_cents:centsFromInput(form.elements.amount.value),
      frequency:form.elements.frequency.value,
      next_payday:form.elements.next_payday.value,
      active:form.elements.active.checked
    })});
    const active = form.elements.active.checked;
    form.reset();
    form.elements.frequency.value = "biweekly";
    form.elements.active.checked = active;
    await refresh();
    showStatus("Income source saved.");
  } catch (error) { showStatus(error.message, true); }
  finally { button.disabled = false; }
});
const today = new Date();
document.getElementById("income-next").value =
  [today.getFullYear(), String(today.getMonth()+1).padStart(2,"0"), String(today.getDate()).padStart(2,"0")].join("-");
refresh().catch((error) => showStatus("Could not load saved household data: " + error.message, true));
</script></body></html>"""


@app.get("/api/people", response_model=list[PersonOut])
def list_people():
    with database.connect() as db:
        rows = db.execute(
            "SELECT id, name, default_buffer_cents, active, created_at "
            "FROM persons ORDER BY id"
        ).fetchall()
    return [_person_out(row) for row in rows]


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
            "SELECT id, name, default_buffer_cents, active, created_at "
            "FROM persons WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return _person_out(row)


def _person_out(row) -> dict:
    return dict(row) | {"active": bool(row["active"])}


@app.patch("/api/people/{person_id}", response_model=PersonOut)
def update_person(person_id: int, body: PersonUpdate):
    updates = body.model_dump(exclude_unset=True)
    if "name" in updates:
        name = updates["name"].strip()
        if not name:
            raise HTTPException(status_code=422, detail="name cannot be blank")
        updates["name"] = name
    if "active" in updates:
        updates["active"] = int(updates["active"])
    if not updates:
        raise HTTPException(status_code=422, detail="no fields to update")
    try:
        with database.connect() as db:
            existing = db.execute(
                "SELECT id FROM persons WHERE id = ?", (person_id,)
            ).fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Household member not found")
            set_clause = ", ".join(f"{column} = ?" for column in updates)
            db.execute(
                f"UPDATE persons SET {set_clause} WHERE id = ?",
                (*updates.values(), person_id),
            )
            row = db.execute(
                "SELECT id, name, default_buffer_cents, active, created_at "
                "FROM persons WHERE id = ?",
                (person_id,),
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=422, detail="Update violates a database constraint"
        ) from exc
    return _person_out(row)


@app.delete("/api/people/{person_id}")
def delete_person(person_id: int):
    with database.connect() as db:
        cursor = db.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Household member not found")
    return {"deleted": True, "id": person_id}


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


@app.patch("/api/income-sources/{income_id}", response_model=IncomeSourceOut)
def update_income_source(income_id: int, body: IncomeSourceUpdate):
    updates = body.model_dump(exclude_unset=True)
    if "name" in updates:
        name = updates["name"].strip()
        if not name:
            raise HTTPException(status_code=422, detail="name cannot be blank")
        updates["name"] = name
    if "next_payday" in updates:
        updates["next_payday"] = updates["next_payday"].isoformat()
    if "active" in updates:
        updates["active"] = int(updates["active"])
    if not updates:
        raise HTTPException(status_code=422, detail="no fields to update")
    try:
        with database.connect() as db:
            existing = db.execute(
                "SELECT id FROM income_sources WHERE id = ?", (income_id,)
            ).fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Income source not found")
            if "person_id" in updates:
                person = db.execute(
                    "SELECT id FROM persons WHERE id = ?", (updates["person_id"],)
                ).fetchone()
                if person is None:
                    raise HTTPException(
                        status_code=404, detail="Household member not found"
                    )
            set_clause = ", ".join(f"{column} = ?" for column in updates)
            db.execute(
                f"UPDATE income_sources SET {set_clause} WHERE id = ?",
                (*updates.values(), income_id),
            )
            row = db.execute(
                """SELECT id, person_id, name, net_amount_cents, frequency,
                          next_payday, active, created_at
                   FROM income_sources WHERE id = ?""",
                (income_id,),
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=422, detail="Update violates a database constraint"
        ) from exc
    return dict(row) | {"active": bool(row["active"])}


@app.delete("/api/income-sources/{income_id}")
def delete_income_source(income_id: int):
    with database.connect() as db:
        cursor = db.execute("DELETE FROM income_sources WHERE id = ?", (income_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Income source not found")
    return {"deleted": True, "id": income_id}


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

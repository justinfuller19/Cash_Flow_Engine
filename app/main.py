"""CashFlow Engine FastAPI entry point."""
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response

from . import database
from .engine import (
    Bill, BufferReservation, CreditCard, Income, PaydayRequest, SavingsGoal,
    generate_payday_plan,
)
from .household_schemas import (
    IncomeAmountOverrideCreate, IncomeAmountOverrideOut,
    IncomeSourceCreate, IncomeSourceOut, IncomeSourceUpdate,
    PersonCreate, PersonOut, PersonUpdate,
)
from .schemas import PaydayPlanOut, PaydayPreviewRequest
from .obligations import router as obligations_router
from .savings import router as savings_router
from .planning import router as planning_router
from .dashboard import router as dashboard_router
from .transactions import router as transactions_router
from .categories import router as categories_router
from .cash_flow import router as cash_flow_router
from .settings_data import router as settings_data_router
from .application_update import router as application_update_router
from .household import router as household_router
from .security import remote_access_guard
from .auth import (household_auth_guard, initialize_auth_database, login_page, register_page, login_route, register_route, logout_route, account_page, account_update_route)
from .ui_refresh import enhance_html


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_auth_database()
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
app.include_router(categories_router)
app.include_router(cash_flow_router)
app.include_router(settings_data_router)
app.include_router(application_update_router)
app.include_router(household_router)
app.middleware("http")(household_auth_guard)
app.middleware("http")(remote_access_guard)

@app.middleware("http")
async def refresh_html_pages(request: Request, call_next):
    """Apply the shared responsive design to browser pages only."""
    response = await call_next(request)
    if request.url.path in {"/docs", "/redoc", "/openapi.json"}:
        return response
    if "text/html" not in response.headers.get("content-type", ""):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    try:
        html = body.decode("utf-8")
    except UnicodeDecodeError:
        # The original streaming iterator has been consumed, so rebuild the
        # response body rather than returning an exhausted response object.
        headers = {
            key: value for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-encoding"}
        }
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.headers.get("content-type", "text/html"),
            background=response.background,
        )
    html = enhance_html(html, include_navigation=request.url.path not in {"/login", "/register"})
    headers = {
        key: value for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-encoding"}
    }
    return HTMLResponse(
        content=html,
        status_code=response.status_code,
        headers=headers,
        background=response.background,
    )



@app.get("/login", response_class=HTMLResponse)
def login_page_route():
    return login_page()


@app.post("/login")
async def login_post(request: Request):
    return await login_route(request)


@app.get("/register", response_class=HTMLResponse)
def register_page_route():
    return register_page()


@app.post("/register")
async def register_post(request: Request):
    return await register_route(request)


@app.get("/logout")
async def logout_get(request: Request):
    return await logout_route(request)


@app.post("/logout")
async def logout_post(request: Request):
    return await logout_route(request)


@app.get("/account", response_class=HTMLResponse)
def account_get():
    return account_page()


@app.post("/account", response_class=HTMLResponse)
async def account_post(request: Request):
    return await account_update_route(request)


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
.app-header{background:#0f3d4a;color:#f8fafc;padding:22px max(18px,calc((100% - 1100px)/2));margin:-24px -14px 28px;box-shadow:0 8px 24px #0f3d4a20}
.brand-row{max-width:1100px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap}
.brand{margin:0;color:#fff;font-size:1.55rem;letter-spacing:-.035em}
.brand-subtitle{margin:5px 0 0;color:#c5dce2;font-size:.94rem}
.account-links{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.account-links a{color:#f8fafc;padding:8px 11px;border:1px solid #5b8791;border-radius:8px;font-size:.9rem}
nav{max-width:1100px;margin:22px auto 0;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.nav-group{min-width:0;padding:12px;background:#174b59;border:1px solid #316775;border-radius:12px}
.nav-label{display:block;color:#b7d3da;font-size:.72rem;font-weight:800;text-transform:uppercase;letter-spacing:.09em;margin:0 0 8px}
.nav-links{display:flex;flex-wrap:wrap;gap:6px}
nav a{display:inline-flex;align-items:center;min-height:36px;background:transparent;color:#f8fafc;padding:8px 10px;border-radius:7px;font-size:.88rem;font-weight:650}
nav a:hover,nav a:focus-visible{background:#286777;outline:2px solid #a5f3fc;outline-offset:1px}
nav a[aria-current="page"]{background:#cffafe;color:#164e63}
@media(max-width:760px){.app-header{padding:18px 14px;margin:-24px -14px 24px}nav{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.nav-group{padding:10px}.nav-links{gap:4px}nav a{padding:8px;font-size:.84rem}}
@media(max-width:420px){nav{grid-template-columns:1fr}.brand-row{align-items:flex-start}.account-links{width:100%}}
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
<header class="app-header">
<div class="brand-row">
<div><h1 class="brand">CashFlow Engine</h1><p class="brand-subtitle">A clearer view of your household money.</p></div>
<div class="account-links"><a href="/account">Account settings</a><a href="/logout">Log out</a></div>
</div>
<nav aria-label="Main navigation">
<section class="nav-group"><span class="nav-label">Overview</span><div class="nav-links"><a href="/" aria-current="page">Dashboard</a><a href="/weekly">Weekly report</a></div></section>
<section class="nav-group"><span class="nav-label">Plan & Budget</span><div class="nav-links"><a href="/setup">Household & income</a><a href="/bills">Bills & debt</a><a href="/savings">Savings goals</a></div></section>
<section class="nav-group"><span class="nav-label">Track Activity</span><div class="nav-links"><a href="/checklist">Payday checklist</a><a href="/transactions">Transactions</a></div></section>
<section class="nav-group"><span class="nav-label">Manage</span><div class="nav-links"><a href="/settings-data">Settings & data</a><a href="/docs">API docs</a></div></section>
</nav>
</header>
<p class="muted">Your household cash-flow overview. Forecasts help you plan; they do not send payments or move money.</p>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<div id="metrics" class="metrics"></div>
<div class="card" style="margin:18px 0">
<h2>Confirm cash on hand</h2>
<p class="muted">Set the actual household cash balance as of the first day of a financial week. Transactions dated after that snapshot are included separately in the forecast. This is a planning balance; it does not connect to your bank.</p>
<form id="dashboard-cash-snapshot-form" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;align-items:end">
<div><label for="dashboard-cash-amount">Cash on hand ($)</label><input id="dashboard-cash-amount" name="amount" type="number" min="0" step="0.01" required style="width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font:inherit"></div>
<div><label for="dashboard-cash-week">Effective week starts</label><input id="dashboard-cash-week" name="effective_week" type="date" required style="width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font:inherit"></div>
<div><button type="submit">Confirm cash balance</button></div>
</form>
<p id="dashboard-cash-snapshot-estimate" class="muted" role="status">Checking whether an estimate is available…</p>
<button id="dashboard-cash-use-estimate" type="button" hidden>Use estimated balance (not saved)</button>
<p id="dashboard-cash-snapshot-status" class="muted" role="status">Loading cash confirmation…</p>
</div>
<div class="grid">
<section class="card"><h2>Weekly cash flow</h2><div id="weekly-summary" class="muted">Loading forecast…</div><h3>Recorded spending by category</h3><ul id="weekly-category-spending" class="list"><li class="muted">Loading…</li></ul><p><a href="/weekly">Review safe surplus and allocate money →</a></p></section>
<section class="card"><h2>Next paydays</h2><ul id="paydays" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Upcoming bills · 30 days</h2><ul id="bills" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Debt payments · 30 days</h2><ul id="debts" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Savings progress</h2><ul id="goals" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Automatic weekly checklist</h2><p class="muted">This week's actions are generated from your saved schedules and safe cash-flow forecast.</p><a href="/checklist">Open this week's checklist →</a></section>
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
const metricValues=[["Active household members",String(d.active_household_members)],["Safe to spend (13-week forecast)",dollars(d.safe_to_spend_cents)],["Actions needing attention",String(d.pending_checklist_count)],["Recorded income this month",dollars(d.month_income_cents)],["Recorded expenses this month",dollars(d.month_expenses_cents)]];
if(d.projected_cash_shortfall_cents>0)metricValues.push(["Projected cash shortfall",dollars(d.projected_cash_shortfall_cents)]);
for(const [label,value] of metricValues){const card=el("div","metric","");card.appendChild(el("span","",label));card.appendChild(el("strong","",value));metrics.appendChild(card);}
renderList("paydays",d.next_paydays,"No upcoming paydays found.",(li,x)=>{li.appendChild(el("div","item-title",x.name+" · "+dollars(x.amount_cents)));li.appendChild(el("div","detail",x.date));});
renderList("bills",d.upcoming_bills,"No saved bills due in the next 30 days.",(li,x)=>{li.appendChild(el("div","item-title",x.name+" · "+dollars(x.amount_cents)));li.appendChild(el("div","detail",x.date+" · "+x.category+(x.autopay?" · autopay":"")));});
renderList("debts",d.upcoming_debts,"No debt due dates found in the next 30 days.",(li,x)=>{li.appendChild(el("div","item-title",x.name+" · minimum "+dollars(x.minimum_payment_cents)));li.appendChild(el("div","detail",x.date+" · balance "+dollars(x.balance_cents)));});
renderList("goals",d.savings_goals,"No active savings goals.",(li,x)=>{const ongoing=x.target_amount_cents===0;const savedText=ongoing?dollars(x.current_balance_cents)+" saved · ongoing":dollars(x.current_balance_cents)+" / "+dollars(x.target_amount_cents);li.appendChild(el("div","item-title",x.name+" · "+savedText));li.appendChild(el("div","detail",(ongoing?"Ongoing savings":x.progress_percent+"% saved")+" · "+dollars(x.weekly_target_cents||0)+" per financial week"));if(!ongoing){const track=el("div","progress","");const fill=el("div","fill","");fill.style.width=x.progress_percent+"%";track.appendChild(fill);li.appendChild(track);}});

document.getElementById("checklist-summary").textContent=d.pending_checklist_count?d.pending_checklist_count+" pending or attention-needed action(s).":"No pending checklist actions.";
renderList("transactions",d.recent_transactions,"No transactions recorded yet.",(li,x)=>{const sign=x.amount_cents<0?"−":"";li.appendChild(el("div","item-title",x.description+" · "+sign+dollars(Math.abs(x.amount_cents))));li.appendChild(el("div","detail",x.transaction_date+" · "+x.category+(x.account_name?" · "+x.account_name:"")));});
try{const weekly=await api("/api/cash-flow/weekly");const root=document.getElementById("weekly-summary");root.replaceChildren();
root.appendChild(el("div","item-title",dollars(weekly.free_to_spend_cents)+" safe available surplus"));
root.appendChild(el("div","detail","After upcoming obligations, planned savings, existing allocations, and future daily cash checkpoints."));
root.appendChild(el("div","detail","Suggested debt snowball: "+(weekly.recommended_snowball_debt?weekly.recommended_snowball_debt.name:"Add a debt to get a recommendation")+" · Allocated so far "+dollars(weekly.planned_allocations_cents)));
root.appendChild(el("div","detail","Starting cash "+dollars(weekly.starting_cash_cents)+" · expected income "+dollars(weekly.expected_income_cents)));
if(weekly.starting_cash_is_stale)root.appendChild(el("div","detail",weekly.starting_cash_confirmed_at?"Warning: starting cash was last confirmed "+weekly.starting_cash_confirmed_at+". Please reconfirm it in Household & income.":"Warning: no starting-cash snapshot is confirmed yet. Set it in Household & income."));
root.appendChild(el("div","detail","Bills still due "+dollars(weekly.bills_due_cents)+" · minimum debt payments "+dollars(weekly.minimum_debt_payments_cents)));
root.appendChild(el("div","detail","You choose how much to allocate to debt, savings, or leave unallocated."));
const categoryRoot=document.getElementById("weekly-category-spending");categoryRoot.replaceChildren();
const topCategories=weekly.category_spending.slice(0,3);
if(!topCategories.length)categoryRoot.appendChild(el("li","muted","No recorded expenses this week."));
else for(const item of topCategories){const li=el("li","");li.appendChild(el("div","item-title",item.category.split(" ").map(w=>w.charAt(0).toUpperCase()+w.slice(1)).join(" ")+" · "+dollars(item.amount_cents)));categoryRoot.appendChild(li);}
if(weekly.projected_shortfall_cents>0)root.appendChild(el("div","detail","A daily cash shortfall of "+dollars(weekly.projected_shortfall_cents)+" is projected before later income arrives."));
}catch(error){document.getElementById("weekly-summary").textContent="Weekly forecast unavailable: "+error.message;}
}
async function loadCashSnapshotControl(){
  const settings=await api("/api/household/settings");
  const amount=document.getElementById("dashboard-cash-amount");
  const week=document.getElementById("dashboard-cash-week");
  const weekdays={Monday:1,Tuesday:2,Wednesday:3,Thursday:4,Friday:5,Saturday:6,Sunday:0};
  const today=new Date();const targetDay=weekdays[settings.financial_week_start||"Friday"];
  today.setDate(today.getDate()-((today.getDay()-targetDay+7)%7));
  const currentWeek=today.getFullYear()+"-"+String(today.getMonth()+1).padStart(2,"0")+"-"+String(today.getDate()).padStart(2,"0");
  week.value=currentWeek;
  amount.value=settings.starting_cash_effective_week===currentWeek?(settings.starting_cash_cents/100).toFixed(2):"";
  const status=document.getElementById("dashboard-cash-snapshot-status");
  const estimate=document.getElementById("dashboard-cash-snapshot-estimate");
  const useEstimate=document.getElementById("dashboard-cash-use-estimate");
  if(settings.suggested_cash_balance_cents!==null&&settings.suggested_cash_balance_cents!==undefined){
    estimate.textContent="Estimate for the week starting "+settings.suggested_cash_effective_week+": "+(settings.suggested_cash_balance_cents/100).toLocaleString(undefined,{style:"currency",currency:"USD"})+" based on "+settings.suggested_cash_transaction_count+" recorded transaction(s) since the snapshot dated "+settings.suggested_cash_based_on_effective_week+". This estimate only uses recorded transactions; verify it against actual cash.";
    useEstimate.hidden=false;
    useEstimate.onclick=()=>{amount.value=(settings.suggested_cash_balance_cents/100).toFixed(2);week.value=settings.suggested_cash_effective_week;status.textContent="Estimate copied into the form only. Check actual cash, then choose Confirm cash balance to save it.";status.style.color="#9a3412";};
  }else{
    estimate.textContent=settings.starting_cash_effective_week===currentWeek?"A confirmed snapshot already exists for the current financial week. Recorded transactions in this week remain separate from that starting balance.":"No estimate is available yet. Confirm a cash balance for this week, or record the transactions since your previous snapshot.";
    useEstimate.hidden=true;
    useEstimate.onclick=null;
  }
  if(settings.starting_cash_confirmed_at){
    status.textContent="Last confirmed "+settings.starting_cash_confirmed_at+" · effective week "+settings.starting_cash_effective_week+(settings.starting_cash_is_stale?" · OUT OF DATE — please confirm the current balance.":".");
    status.style.color=settings.starting_cash_is_stale?"#b91c1c":"#166534";
  }else{
    status.textContent="No cash snapshot has been confirmed. Enter the balance and effective week to establish your first snapshot.";
    status.style.color="#b91c1c";
  }
}
document.getElementById("dashboard-cash-snapshot-form").addEventListener("submit",async event=>{
  event.preventDefault();const form=event.currentTarget;const button=form.querySelector("button");button.disabled=true;
  const status=document.getElementById("dashboard-cash-snapshot-status");
  try{
    const amount=Number(form.elements.amount.value);
    if(!Number.isFinite(amount)||amount<0)throw Error("Enter a cash balance of zero or more.");
    const response=await fetch("/api/household/settings",{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({starting_cash_cents:Math.round((amount+Number.EPSILON)*100),starting_cash_effective_week:form.elements.effective_week.value})});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw Error(data.detail||"Could not confirm the cash balance.");
    status.textContent="Cash balance confirmed for the week starting "+data.starting_cash_effective_week+".";
    status.style.color="#166534";
    await load();await loadCashSnapshotControl();
  }catch(error){status.textContent=error.message;status.style.color="#b91c1c";}
  finally{button.disabled=false;}
});
load().catch(e=>{statusBox.textContent=e.message;statusBox.classList.add("error");statusBox.hidden=false;});
loadCashSnapshotControl().catch(e=>{const s=document.getElementById("dashboard-cash-snapshot-status");s.textContent="Cash confirmation unavailable: "+e.message;s.style.color="#b91c1c";});
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
<section class="card"><h2>Starting cash</h2><p class="muted">Confirm cash on hand at the beginning of a financial week; transactions recorded during that week are then reflected in the forecast separately.</p>
<p id="cash-confirmation-status" class="muted" role="status">Loading cash confirmation…</p>
<form id="settings-form">
<label for="settings-cash">Cash on hand at week start ($)</label><input id="settings-cash" name="starting_cash" type="number" min="0" step="0.01" value="0.00" inputmode="decimal" required>
<label for="settings-cash-week">Effective financial week starts</label><input id="settings-cash-week" name="starting_cash_effective_week" type="date" required>
<p class="muted">Choose the first day of the financial week. Confirmed snapshots are saved with their effective date so older weekly reports can use the matching balance. Confirm again if the balance is more than 10 days old.</p>
<button type="submit">Save household settings</button></form>
</section>
<section class="card"><h2>Household members</h2><p class="muted">Add the people in your household. Income sources are linked to a member below.</p>
<form id="person-form">
<label for="person-name">Name</label><input id="person-name" name="name" maxlength="120" autocomplete="name" required>
<button type="submit">Add household member</button></form>
<ul id="people-list" class="list" aria-label="Saved household members"><li class="empty">Loading members…</li></ul>
</section>
<section class="card"><h2>Income sources</h2><p class="muted">Record net take-home pay and the next expected payday.</p>
<form id="income-form">
<label for="income-person">Household member</label><select id="income-person" name="person_id" required><option value="">Add a member first</option></select>
<label for="income-name">Income source</label><input id="income-name" name="name" maxlength="120" placeholder="e.g. Main paycheck" required>
<label for="income-amount">Net amount per paycheck ($)</label><input id="income-amount" name="amount" type="number" min="0" step="0.01" inputmode="decimal" required>
<label for="income-frequency">Pay frequency</label><select id="income-frequency" name="frequency" required>
<option value="weekly">Weekly</option><option value="biweekly" selected>Every two weeks</option><option value="semimonthly">1st and 15th</option><option value="twice_monthly_last">15th and last calendar day</option><option value="first_third_friday">1st and 3rd Friday</option><option value="last_business_day">Last business day</option><option value="monthly">Monthly (same date)</option><option value="irregular">One-time / irregular</option></select>
<label for="income-next">Next payday</label><input id="income-next" name="next_payday" type="date" required>
<label for="income-end">Last payday (optional)</label><input id="income-end" name="end_date" type="date"><p class="muted">Leave blank if this income is expected to continue indefinitely.</p>
<label><input id="income-active" name="active" type="checkbox" checked style="width:auto;margin-right:8px">This income is active</label>
<button type="submit">Add income source</button></form>
<ul id="income-list" class="list" aria-label="Saved income sources"><li class="empty">Loading income sources…</li></ul>
</section></div>
<p class="muted">Continue with Bills & Debt, Savings Goals, or your automatically generated Checklist. This screen does not connect to your bank or move money.</p>
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
    addSmallButton(item, "Edit member", async () => {
      const name = prompt("Household member name:", person.name);
      if (name === null) return;
      try {
        await api("/api/people/" + person.id, {method:"PATCH", body:JSON.stringify({
          name:name.trim()
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
      income.frequency + " · next payday " + income.next_payday +
      (income.end_date ? " · ends " + income.end_date : " · no end date") + (income.active ? "" : " · inactive"));
    addSmallButton(item, "Edit income", async () => {
      const name = prompt("Income source name:", income.name);
      if (name === null) return;
      const amount = prompt("Net amount per paycheck in dollars:", (income.net_amount_cents / 100).toFixed(2));
      if (amount === null) return;
      const frequency = prompt("Frequency (weekly, biweekly, semimonthly, monthly, irregular):", income.frequency);
      if (frequency === null) return;
      const nextPayday = prompt("Next payday YYYY-MM-DD:", income.next_payday);
      if (nextPayday === null) return;
      const endDate = prompt("Last scheduled payday YYYY-MM-DD (leave blank for no end date):", income.end_date || "");
      if (endDate === null) return;
      try {
        await api("/api/income-sources/" + income.id, {method:"PATCH", body:JSON.stringify({
          name:name.trim(), net_amount_cents:centsFromInput(amount),
          frequency:frequency.trim(), next_payday:nextPayday,
          end_date:endDate.trim() || null
        })});
        await refresh(); showStatus("Income source updated.");
      } catch (error) { showStatus(error.message, true); }
    }, true);
    addSmallButton(item, "Temporary amount override", async () => {
      const startDate = prompt("Override start date (YYYY-MM-DD):");
      if (!startDate) return;
      const endDate = prompt("Override end date (YYYY-MM-DD, inclusive):", startDate);
      if (!endDate) return;
      const amount = prompt("Temporary net amount per paycheck in dollars:", (income.net_amount_cents / 100).toFixed(2));
      if (amount === null) return;
      const reason = prompt("Reason (optional, e.g. bonus or reduced hours):", "") ?? "";
      try {
        await api("/api/income-sources/" + income.id + "/amount-overrides", {method:"POST", body:JSON.stringify({
          start_date:startDate, end_date:endDate, amount_cents:centsFromInput(amount), reason
        })});
        await refresh(); showStatus("Temporary amount override saved.");
      } catch (error) { showStatus(error.message, true); }
    }, true);
    addSmallButton(item, "Manage overrides", async () => {
      try {
        const overrides = await api("/api/income-sources/" + income.id + "/amount-overrides");
        if (!overrides.length) { showStatus("No temporary overrides are set for " + income.name + "."); return; }
        const choices = overrides.map(x => x.id + ": " + x.start_date + " to " + x.end_date + " · " + dollars(x.amount_cents) + (x.reason ? " · " + x.reason : "")).join("\\n");
        const selected = prompt("Saved temporary overrides (enter an ID to delete one):\\n" + choices);
        if (!selected) return;
        await api("/api/income-sources/" + income.id + "/amount-overrides/" + Number(selected), {method:"DELETE"});
        await refresh(); showStatus("Temporary amount override deleted.");
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
  try {
    const settings = await api("/api/household/settings");
    document.getElementById("settings-cash").value = (settings.starting_cash_cents / 100).toFixed(2);
    const weekInput = document.getElementById("settings-cash-week");
    const today = new Date();
    const weekdays = {Monday:1, Tuesday:2, Wednesday:3, Thursday:4, Friday:5, Saturday:6, Sunday:0};
    const targetDay = weekdays[settings.financial_week_start || "Friday"];
    const delta = (today.getDay() - targetDay + 7) % 7;
    today.setDate(today.getDate() - delta);
    const currentWeek = today.getFullYear()+"-"+String(today.getMonth()+1).padStart(2,"0")+"-"+String(today.getDate()).padStart(2,"0");
    weekInput.value = settings.starting_cash_effective_week || currentWeek;
    const confirmation = document.getElementById("cash-confirmation-status");
    if (settings.starting_cash_confirmed_at) {
      confirmation.textContent = "Last confirmed "+settings.starting_cash_confirmed_at+" · effective week "+settings.starting_cash_effective_week+" · balance "+(settings.starting_cash_cents/100).toLocaleString(undefined,{style:"currency",currency:"USD"})+(settings.starting_cash_is_stale?" · OUT OF DATE — please confirm the current balance.":".");
      confirmation.style.color = settings.starting_cash_is_stale ? "#b91c1c" : "#166534";
    } else {
      confirmation.textContent = "No cash balance has been confirmed yet. Set the amount and effective week to establish your first snapshot.";
      confirmation.style.color = "#b91c1c";
    }
  } catch (error) { showStatus("Could not load household settings: " + error.message, true); }
}
document.getElementById("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    await api("/api/household/settings", {method:"PATCH", body:JSON.stringify({
      starting_cash_cents:centsFromInput(form.elements.starting_cash.value),
      starting_cash_effective_week:form.elements.starting_cash_effective_week.value
    })});
    await refresh();
    showStatus("Household settings saved. Cash snapshot recorded for the selected financial week.");
  } catch (error) { showStatus(error.message, true); }
  finally { button.disabled = false; }
});
document.getElementById("person-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    await api("/api/people", {method:"POST", body:JSON.stringify({
      name:form.elements.name.value.trim()
    })});
    form.reset();
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
      end_date:form.elements.end_date.value || null,
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
            "SELECT id, name, active, created_at "
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
            "INSERT INTO persons (name) VALUES (?)",
            (name,)
        )
        row = db.execute(
            "SELECT id, name, active, created_at "
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
                "SELECT id, name, active, created_at "
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
                      next_payday, active, end_date, created_at
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
                   (person_id, name, net_amount_cents, frequency, next_payday, active, end_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    body.person_id, name, body.net_amount_cents, body.frequency,
                    body.next_payday.isoformat(), int(body.active),
                    body.end_date.isoformat() if body.end_date else None,
                ),
            )
            row = db.execute(
                """SELECT id, person_id, name, net_amount_cents, frequency,
                          next_payday, active, end_date, created_at
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
    if "end_date" in updates and updates["end_date"] is not None:
        updates["end_date"] = updates["end_date"].isoformat()
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
                          next_payday, active, end_date, created_at
                   FROM income_sources WHERE id = ?""",
                (income_id,),
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=422, detail="Update violates a database constraint"
        ) from exc
    return dict(row) | {"active": bool(row["active"])}



@app.get("/api/income-sources/{income_id}/amount-overrides", response_model=list[IncomeAmountOverrideOut])
def list_income_amount_overrides(income_id: int):
    with database.connect() as db:
        exists = db.execute("SELECT id FROM income_sources WHERE id = ?", (income_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Income source not found")
        rows = db.execute(
            """SELECT id, income_source_id, start_date, end_date, amount_cents, reason, created_at
               FROM income_amount_overrides WHERE income_source_id = ?
               ORDER BY start_date, end_date, id""",
            (income_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/income-sources/{income_id}/amount-overrides", response_model=IncomeAmountOverrideOut, status_code=status.HTTP_201_CREATED)
def create_income_amount_override(income_id: int, body: IncomeAmountOverrideCreate):
    try:
        with database.connect() as db:
            exists = db.execute("SELECT id FROM income_sources WHERE id = ?", (income_id,)).fetchone()
            if exists is None:
                raise HTTPException(status_code=404, detail="Income source not found")
            overlap = db.execute(
                """SELECT id FROM income_amount_overrides
                   WHERE income_source_id = ? AND start_date <= ? AND end_date >= ?""",
                (income_id, body.end_date.isoformat(), body.start_date.isoformat()),
            ).fetchone()
            if overlap:
                raise HTTPException(status_code=409, detail="This date range overlaps an existing amount override")
            cursor = db.execute(
                """INSERT INTO income_amount_overrides
                   (income_source_id, start_date, end_date, amount_cents, reason)
                   VALUES (?, ?, ?, ?, ?)""",
                (income_id, body.start_date.isoformat(), body.end_date.isoformat(),
                 body.amount_cents, body.reason.strip()),
            )
            row = db.execute(
                """SELECT id, income_source_id, start_date, end_date, amount_cents, reason, created_at
                   FROM income_amount_overrides WHERE id = ?""", (cursor.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Amount override violates a database constraint") from exc
    return dict(row)


@app.delete("/api/income-sources/{income_id}/amount-overrides/{override_id}")
def delete_income_amount_override(income_id: int, override_id: int):
    with database.connect() as db:
        cursor = db.execute(
            "DELETE FROM income_amount_overrides WHERE id = ? AND income_source_id = ?",
            (override_id, income_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Amount override not found")
    return {"deleted": True, "id": override_id}


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

"""Weekly household cash-flow forecast from saved schedules and transactions."""
from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from . import database
from .forecast import occurrences_between

router = APIRouter()


def _date(value) -> date | None:
    return date.fromisoformat(value) if value else None


def _monthly_due_dates(day: int | None, start: date, end: date) -> list[date]:
    if not day:
        return []
    results = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        due = date(cursor.year, cursor.month, min(day, monthrange(cursor.year, cursor.month)[1]))
        if start <= due <= end:
            results.append(due)
        next_month = cursor.month + 1
        cursor = date(cursor.year + (next_month > 12), 1 if next_month > 12 else next_month, 1)
    return results


def _within_window(left: date, right: date, days: int) -> bool:
    return abs((left - right).days) <= days


def _take_matching_transaction(
    transactions: list[dict],
    used_ids: set[int],
    link_type: str,
    link_id: int,
    due_date: date,
    window_days: int,
) -> dict | None:
    candidates = [
        item for item in transactions
        if item["id"] not in used_ids
        and item["linked_obligation_type"] == link_type
        and item["linked_obligation_id"] == link_id
        and _within_window(date.fromisoformat(item["transaction_date"]), due_date, window_days)
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            abs((date.fromisoformat(item["transaction_date"]) - due_date).days),
            item["transaction_date"],
            item["id"],
        )
    )
    match = candidates[0]
    used_ids.add(match["id"])
    return match


@router.get("/api/cash-flow/weekly")
def weekly_cash_flow(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    forecast_weeks: int = 13,
):
    """Forecast the selected window and reserve cash for later weeks' obligations."""
    start = start_date or date.today()
    end = end_date or (start + timedelta(days=6))
    if end < start:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    if (end - start).days > 30:
        raise HTTPException(status_code=422, detail="Cash-flow forecast window cannot exceed 31 days")
    if isinstance(forecast_weeks, bool) or not isinstance(forecast_weeks, int) or not 1 <= forecast_weeks <= 52:
        raise HTTPException(status_code=422, detail="forecast_weeks must be between 1 and 52")

    report = _weekly_cash_flow_for_window(start, end)
    buffer_target = report["buffer_target_cents"]
    current_ending_cash = (
        report["projected_cash_before_buffer_cents"] - report["funded_savings_cents"]
    )
    lowest_projected_cash = current_ending_cash
    future_forecast = []
    carried_cash = current_ending_cash
    future_start = end + timedelta(days=1)

    # Carry each period's ending cash forward. Re-read schedules and transactions
    # for every future window so each request reflects current data.
    for offset in range(1, forecast_weeks):
        future_end = future_start + timedelta(days=6)
        future = _weekly_cash_flow_for_window(
            future_start, future_end, starting_cash_override=carried_cash
        )
        ending_cash = (
            future["projected_cash_before_buffer_cents"] - future["funded_savings_cents"]
        )
        lowest_projected_cash = min(lowest_projected_cash, ending_cash)
        future_forecast.append({
            "start_date": future_start.isoformat(),
            "end_date": future_end.isoformat(),
            "starting_cash_cents": carried_cash,
            "expected_income_cents": future["expected_income_cents"],
            "recorded_income_cents": future["recorded_income_cents"],
            "bills_due_cents": future["bills_due_cents"],
            "minimum_debt_payments_cents": future["minimum_debt_payments_cents"],
            "transaction_expenses_cents": future["transaction_expenses_cents"],
            "projected_ending_cash_cents": ending_cash,
            "projected_shortfall_cents": max(0, -ending_cash),
            "buffer_shortfall_cents": max(0, buffer_target - ending_cash),
        })
        carried_cash = ending_cash
        future_start = future_end + timedelta(days=1)

    # The lowest balance in the entire horizon limits discretionary spending now.
    safe_to_spend = max(0, lowest_projected_cash - buffer_target)
    report["projected_ending_cash_cents"] = current_ending_cash
    report["free_to_spend_before_future_reserve_cents"] = report["free_to_spend_cents"]
    report["future_buffer_reservation_cents"] = max(
        0, report["free_to_spend_cents"] - safe_to_spend
    )
    report["free_to_spend_cents"] = safe_to_spend
    report["extra_debt_payoff_capacity_cents"] = safe_to_spend
    report["forecast_weeks"] = forecast_weeks
    report["lowest_projected_cash_cents"] = lowest_projected_cash
    report["future_buffer_shortfall_cents"] = max(0, buffer_target - lowest_projected_cash)
    report["future_forecast"] = future_forecast
    report["notes"].append(
        "Safe-to-spend reserves cash against the lowest projected ending balance across "
        + str(forecast_weeks)
        + " forecast week(s); future weeks carry forward prior ending cash."
    )
    return report


def _weekly_cash_flow_for_window(
    start: date,
    end: date,
    starting_cash_override: int | None = None,
) -> dict:
    """Calculate one period without lookahead, optionally carrying prior cash."""
    with database.connect() as db:
        settings = db.execute(
            "SELECT starting_cash_cents, buffer_target_cents FROM household_settings WHERE id = 1"
        ).fetchone()
        starting_cash = (
            starting_cash_override
            if starting_cash_override is not None
            else (settings["starting_cash_cents"] if settings else 0)
        )
        buffer_target = settings["buffer_target_cents"] if settings else 0

        # Include a short look-back for payments recorded just before the window
        # so they can mark a scheduled obligation as already covered.
        match_start = start - timedelta(days=31)
        transaction_rows = db.execute(
            """SELECT id, transaction_date, transaction_type, category, description,
                      amount_cents, linked_obligation_type, linked_obligation_id
               FROM transactions
               WHERE transaction_date >= ? AND transaction_date <= ?
               ORDER BY transaction_date, id""",
            (match_start.isoformat(), end.isoformat()),
        ).fetchall()
        all_transactions = [dict(row) for row in transaction_rows]
        in_period = [
            row for row in all_transactions
            if start.isoformat() <= row["transaction_date"] <= end.isoformat()
        ]

        # Scheduled income occurrences. A linked actual paycheck replaces its
        # matching expected occurrence, rather than being counted a second time.
        income_occurrences = []
        rows = db.execute(
            """SELECT i.id, i.name, i.net_amount_cents, i.frequency, i.next_payday,
                      p.name AS person_name
               FROM income_sources i JOIN persons p ON p.id = i.person_id
               WHERE i.active = 1 AND p.active = 1
               ORDER BY i.next_payday, i.id"""
        ).fetchall()
        for row in rows:
            anchor = date.fromisoformat(row["next_payday"])
            for payday in occurrences_between(anchor, row["frequency"], start, end):
                income_occurrences.append({
                    "source_id": row["id"],
                    "name": f'{row["person_name"]} — {row["name"]}',
                    "date": payday.isoformat(),
                    "amount_cents": row["net_amount_cents"],
                })
        income_occurrences.sort(key=lambda item: (item["date"], item["name"]))
        used_transactions: set[int] = set()
        expected_income = []
        covered_income = []
        for item in income_occurrences:
            match = _take_matching_transaction(
                all_transactions, used_transactions, "income", item["source_id"],
                date.fromisoformat(item["date"]), 7,
            )
            if match:
                covered_income.append({
                    **item, "covered_by_transaction_id": match["id"],
                    "transaction_date": match["transaction_date"],
                })
            else:
                expected_income.append(item)

        # Bills are expanded only within the selected window. An explicitly
        # linked transaction suppresses one occurrence when it falls within the
        # bill's configured flexible window (at least seven days).
        bills = []
        rows = db.execute(
            """SELECT id, name, amount_cents, next_due_date, frequency,
                      flexible_window_days, category
               FROM bills WHERE active = 1 ORDER BY next_due_date, id"""
        ).fetchall()
        for row in rows:
            anchor = _date(row["next_due_date"])
            if anchor is None:
                continue
            for due in occurrences_between(anchor, row["frequency"], start, end):
                match = _take_matching_transaction(
                    all_transactions, used_transactions, "bill", row["id"], due,
                    max(7, row["flexible_window_days"] or 0),
                )
                bills.append({
                    "id": row["id"], "name": row["name"], "category": row["category"],
                    "due_date": due.isoformat(), "amount_cents": row["amount_cents"],
                    "status": "recorded" if match else "due",
                    "covered_by_transaction_id": match["id"] if match else None,
                    "transaction_date": match["transaction_date"] if match else None,
                })
        bills.sort(key=lambda item: (item["due_date"], item["name"]))

        debts = []
        rows = db.execute(
            """SELECT id, name, minimum_payment_cents, next_due_date, due_day_of_month
               FROM credit_cards WHERE active = 1 ORDER BY id"""
        ).fetchall()
        for row in rows:
            anchor = _date(row["next_due_date"])
            due_dates = (
                occurrences_between(anchor, "monthly", start, end)
                if anchor else _monthly_due_dates(row["due_day_of_month"], start, end)
            )
            for due in due_dates:
                match = _take_matching_transaction(
                    all_transactions, used_transactions, "debt", row["id"], due, 7,
                )
                debts.append({
                    "id": row["id"], "name": row["name"], "due_date": due.isoformat(),
                    "amount_cents": row["minimum_payment_cents"],
                    "status": "recorded" if match else "due",
                    "covered_by_transaction_id": match["id"] if match else None,
                    "transaction_date": match["transaction_date"] if match else None,
                })
        debts.sort(key=lambda item: (item["due_date"], item["name"]))

        goals = db.execute(
            """SELECT id, name, contribution_per_period_cents, priority
               FROM savings_goals WHERE active = 1 AND contribution_per_period_cents > 0
               ORDER BY priority, name COLLATE NOCASE, id"""
        ).fetchall()

    # Recorded cash movements are counted by transaction type, never by category
    # or sign alone. Transfers between household accounts are cash-neutral.
    transaction_income = sum(
        abs(row["amount_cents"]) for row in in_period
        if row["transaction_type"] == "income"
    )
    transaction_expenses = sum(
        abs(row["amount_cents"]) for row in in_period
        if row["transaction_type"] == "expense"
    )
    transaction_transfers = [
        {
            "id": row["id"], "date": row["transaction_date"],
            "description": row["description"], "amount_cents": row["amount_cents"],
        }
        for row in in_period if row["transaction_type"] == "transfer"
    ]

    spending_by_category = {}
    for row in in_period:
        if row["transaction_type"] == "expense":
            category = row["category"]
            spending_by_category[category] = spending_by_category.get(category, 0) + abs(row["amount_cents"])
    category_spending = [
        {"category": category, "amount_cents": amount}
        for category, amount in sorted(
            spending_by_category.items(), key=lambda pair: (-pair[1], pair[0].casefold())
        )
    ]

    uncovered_bills = [item for item in bills if item["status"] == "due"]
    uncovered_debts = [item for item in debts if item["status"] == "due"]
    expected_income_cents = sum(item["amount_cents"] for item in expected_income)
    bills_due_cents = sum(item["amount_cents"] for item in uncovered_bills)
    minimum_debt_cents = sum(item["amount_cents"] for item in uncovered_debts)

    # A savings contribution is planned once per distinct payday date, not once
    # per household member or income source on the same day.
    payday_dates = {item["date"] for item in income_occurrences}
    planned_savings = sum(row["contribution_per_period_cents"] for row in goals) * len(payday_dates)
    projected_cash = (
        starting_cash + expected_income_cents + transaction_income
        - bills_due_cents - minimum_debt_cents - transaction_expenses
    )
    buffer_replenishment = max(0, buffer_target - projected_cash)
    available_after_buffer = max(0, projected_cash - buffer_target)
    funded_savings = min(planned_savings, available_after_buffer)
    savings_shortfall = planned_savings - funded_savings
    free_to_spend = max(0, available_after_buffer - funded_savings)

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "starting_cash_cents": starting_cash,
        "buffer_target_cents": buffer_target,
        "expected_income": expected_income,
        "expected_income_cents": expected_income_cents,
        "recorded_income_cents": transaction_income,
        "covered_income": covered_income,
        "bills": bills,
        "bills_due_cents": bills_due_cents,
        "bills_covered_by_transactions_cents": sum(
            item["amount_cents"] for item in bills if item["status"] == "recorded"
        ),
        "minimum_debt_payments": debts,
        "minimum_debt_payments_cents": minimum_debt_cents,
        "debt_payments_covered_by_transactions_cents": sum(
            item["amount_cents"] for item in debts if item["status"] == "recorded"
        ),
        "transactions": [
            {
                "id": row["id"], "date": row["transaction_date"],
                "type": row["transaction_type"], "category": row["category"],
                "description": row["description"], "amount_cents": row["amount_cents"],
                "linked_obligation_type": row["linked_obligation_type"],
                "linked_obligation_id": row["linked_obligation_id"],
            }
            for row in in_period
        ],
        "transaction_expenses_cents": transaction_expenses,
        "category_spending": category_spending,
        "transfers": transaction_transfers,
        "planned_savings_cents": planned_savings,
        "funded_savings_cents": funded_savings,
        "savings_shortfall_cents": savings_shortfall,
        "projected_cash_before_buffer_cents": projected_cash,
        "buffer_replenishment_cents": buffer_replenishment,
        "projected_shortfall_cents": max(0, -projected_cash),
        "buffer_shortfall_cents": max(0, buffer_target - projected_cash),
        "free_to_spend_cents": free_to_spend,
        "extra_debt_payoff_capacity_cents": free_to_spend,
        "notes": [
            "Only active scheduled income, bills, debts, and savings goals are included.",
            "Link a recorded transaction to the scheduled item it paid or received to prevent duplicate counting.",
            "Linked transactions match scheduled occurrences within seven days, or the bill's larger flexible window.",
            "Transfers are listed for reference but do not change household cash totals.",
            "All monetary calculations use integer cents; this report never initiates payments or transfers.",
        ],
    }


@router.get("/weekly", response_class=HTMLResponse)
def weekly_report_page():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weekly Cash Flow · CashFlow Engine</title>
<style>
:root{font-family:system-ui,sans-serif;color:#172033;background:#f3f6fa}*{box-sizing:border-box}
body{margin:0;padding:24px 14px 48px}main{max-width:1150px;margin:auto}a{color:#0e7490;font-weight:650;text-decoration:none}
h1{color:#155e75;margin:8px 0}h2{font-size:1.1rem;margin:0 0 12px}.muted{color:#5c687a;line-height:1.5}
.toolbar,.card{background:white;padding:20px;border-radius:15px;box-shadow:0 8px 28px #172b4d0d;margin-top:16px}
.toolbar{display:flex;gap:12px;align-items:end;flex-wrap:wrap}.field{flex:1;min-width:150px}
label{display:block;font-size:.9rem;font-weight:650;margin-bottom:5px}input{width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font:inherit}
button{border:0;border-radius:8px;padding:10px 14px;background:#0e7490;color:white;font-weight:700;cursor:pointer}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:10px;margin-top:16px}
.metric{background:white;padding:15px;border-radius:12px;box-shadow:0 5px 18px #172b4d0a;min-width:0}
.metric span{display:block;color:#5c687a;font-size:.88rem}.metric strong{display:block;font-size:1.25rem;color:#155e75;margin-top:5px;overflow-wrap:anywhere}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:16px}
.list{padding:0;list-style:none;margin:0}.list li{padding:12px 0;border-top:1px solid #e2e8f0;overflow-wrap:anywhere}
.list li:first-child{border-top:0}.item-title{font-weight:700}.detail{font-size:.9rem;color:#5c687a;margin-top:4px}
.status{padding:12px;border-radius:9px;background:#e0f2fe;color:#075985;margin-top:16px}.warning{background:#fff7ed;color:#9a3412}.error{background:#fee2e2;color:#991b1b}.status[hidden]{display:none}
</style></head><body><main><a href="/">← Dashboard</a><h1>Weekly Cash Flow</h1>
<p class="muted">Review what is expected, what is already recorded, and what remains after required payments, the household buffer, and planned savings. Forecasts are estimates and never move money.</p>
<div class="toolbar"><div class="field"><label for="start">Start date</label><input id="start" type="date" required></div>
<div class="field"><label for="end">End date</label><input id="end" type="date" required></div><button id="refresh" type="button">Update report</button></div>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<div id="metrics" class="metrics"></div>
<div class="grid">
<section class="card"><h2>Expected income</h2><ul id="income" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Bills due</h2><ul id="bills" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Minimum debt payments</h2><ul id="debts" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Recorded transactions</h2><ul id="transactions" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Spending by category</h2><ul id="category-spending" class="list"><li class="muted">Loading…</li></ul></section>
<section class="card"><h2>Planned savings</h2><div id="savings" class="muted">Loading…</div></section>
<section class="card"><h2>Upcoming weeks & buffer protection</h2><ul id="future-forecast" class="list"><li class="muted">Loading future forecast…</li></ul></section>
<section class="card"><h2>Forecast assumptions</h2><ul id="notes" class="list"></ul></section>
</div></main>
<script>
const statusBox=document.getElementById("status");
function dollars(cents){const sign=cents<0?"−":"";return sign+new Intl.NumberFormat(undefined,{style:"currency",currency:"USD"}).format(Math.abs(cents)/100);}
function el(tag,cls,txt){const n=document.createElement(tag);if(cls)n.className=cls;if(txt!==undefined)n.textContent=txt;return n;}
async function api(url){const r=await fetch(url);const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"Could not load the weekly forecast.");return d;}
function message(text,kind=""){statusBox.textContent=text;statusBox.className="status"+(kind?" "+kind:"");statusBox.hidden=false;}
function renderList(id,items,empty,make){const root=document.getElementById(id);root.replaceChildren();if(!items.length){root.appendChild(el("li","muted",empty));return;}for(const item of items){const li=el("li","");make(li,item);root.appendChild(li);}}
function metric(root,label,value){const card=el("div","metric");card.appendChild(el("span","",label));card.appendChild(el("strong","",value));root.appendChild(card);}
async function load(){const start=document.getElementById("start").value;const end=document.getElementById("end").value;
if(!start||!end)throw Error("Choose both report dates.");
const report=await api("/api/cash-flow/weekly?start_date="+encodeURIComponent(start)+"&end_date="+encodeURIComponent(end));
const metrics=document.getElementById("metrics");metrics.replaceChildren();
for(const [label,value] of [
["Starting cash",report.starting_cash_cents],["Expected income",report.expected_income_cents],
["Recorded income",report.recorded_income_cents],["Bills still due",report.bills_due_cents],
["Minimum debt payments",report.minimum_debt_payments_cents],["Recorded expenses",report.transaction_expenses_cents],
["Projected cash before buffer",report.projected_cash_before_buffer_cents],["Buffer target",report.buffer_target_cents],
["Buffer replenishment needed",report.buffer_replenishment_cents],["Planned savings",report.planned_savings_cents],
["Savings funded",report.funded_savings_cents],
["Available before future reserve",report.free_to_spend_before_future_reserve_cents],
["Reserved for future bills",report.future_buffer_reservation_cents],
["Safe to spend",report.free_to_spend_cents],
["Extra debt payoff capacity",report.extra_debt_payoff_capacity_cents]
])metric(metrics,label,dollars(value));
if(report.projected_shortfall_cents>0)message("Projected shortfall: "+dollars(report.projected_shortfall_cents)+". Required obligations exceed projected cash.","warning");
else if(report.buffer_shortfall_cents>0)message("Projected cash is below the household buffer target by "+dollars(report.buffer_shortfall_cents)+". Extra spending and savings are not available until the buffer is restored.","warning");
else if(report.future_buffer_shortfall_cents>0)message("Future weeks would fall below the household buffer by as much as "+dollars(report.future_buffer_shortfall_cents)+". Safe-to-spend has been reduced to $0 until the forecast gap is addressed.","warning");
else if(report.future_buffer_reservation_cents>0)message(dollars(report.future_buffer_reservation_cents)+" is reserved for upcoming obligations so the projected household balance can stay above its buffer.","warning");
else if(report.savings_shortfall_cents>0)message("Required obligations and the buffer are covered, but planned savings are short by "+dollars(report.savings_shortfall_cents)+".","warning");
else {statusBox.hidden=true;}
renderList("income",report.expected_income,"No scheduled income remains expected in this window.",(li,x)=>{li.appendChild(el("div","item-title",x.name+" · "+dollars(x.amount_cents)));li.appendChild(el("div","detail",x.date));});
const billRows=report.bills.map(x=>({...x,kind:"bill"}));
renderList("bills",billRows,"No bills due in this window.",(li,x)=>{li.appendChild(el("div","item-title",x.name+" · "+dollars(x.amount_cents)));li.appendChild(el("div","detail",x.due_date+" · "+x.status+(x.transaction_date?" · recorded "+x.transaction_date:"")));});
renderList("debts",report.minimum_debt_payments,"No minimum debt payments due in this window.",(li,x)=>{li.appendChild(el("div","item-title",x.name+" · "+dollars(x.amount_cents)));li.appendChild(el("div","detail",x.due_date+" · "+x.status+(x.transaction_date?" · recorded "+x.transaction_date:"")));});
renderList("transactions",report.transactions,"No transactions recorded in this window.",(li,x)=>{li.appendChild(el("div","item-title",x.description+" · "+dollars(x.amount_cents)));li.appendChild(el("div","detail",x.date+" · "+x.type+" · "+x.category+(x.linked_obligation_type?" · linked "+x.linked_obligation_type:"")));});
renderList("category-spending",report.category_spending,"No expense transactions recorded in this window.",(li,x)=>{li.appendChild(el("div","item-title",x.category.split(" ").map(w=>w.charAt(0).toUpperCase()+w.slice(1)).join(" ")+" · "+dollars(x.amount_cents)));});
const savings=document.getElementById("savings");savings.replaceChildren();savings.appendChild(el("p","", "Planned: "+dollars(report.planned_savings_cents)));savings.appendChild(el("p","", "Funded after obligations and buffer: "+dollars(report.funded_savings_cents)));savings.appendChild(el("p","", "Not covered by this forecast: "+dollars(report.savings_shortfall_cents)));savings.appendChild(el("p","muted","Contributions are estimated from active savings goals and distinct scheduled payday dates."));
renderList("notes",report.notes,"No additional assumptions.",(li,note)=>li.appendChild(el("div","detail",note)));
renderList("future-forecast",report.future_forecast,"No future weeks included in this forecast.",(li,x)=>{
  li.appendChild(el("div","item-title",x.start_date+" to "+x.end_date+" · ending cash "+dollars(x.projected_ending_cash_cents)));
  li.appendChild(el("div","detail","Starting cash "+dollars(x.starting_cash_cents)+" · income "+dollars(x.expected_income_cents+x.recorded_income_cents)+" · bills "+dollars(x.bills_due_cents)+" · minimum debt "+dollars(x.minimum_debt_payments_cents)));
  if(x.buffer_shortfall_cents>0)li.appendChild(el("div","detail","Buffer shortfall: "+dollars(x.buffer_shortfall_cents)));
}); 
}
function iso(d){return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0");}
const today=new Date();document.getElementById("start").value=iso(today);const end=new Date(today);end.setDate(end.getDate()+6);document.getElementById("end").value=iso(end);
document.getElementById("refresh").addEventListener("click",()=>load().catch(e=>message(e.message,"error")));
load().catch(e=>message(e.message,"error"));
</script></body></html>"""

"""Legacy weekly calculation retained for internal checklist/reminder compatibility."""
from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from . import database
from .household import CASH_SNAPSHOT_STALE_DAYS
from .forecast import occurrences_between
from .pay_period_service import build_pay_period_forecast, post_due_scheduled_transactions

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


def _take_matching_transactions(
    transactions: list[dict],
    used_ids: set[int],
    link_type: str,
    link_id: int,
    due_date: date,
    window_days: int,
) -> list[dict]:
    """Consume all linked payments eligible for one scheduled obligation cycle."""
    candidates = [
        item for item in transactions
        if item["id"] not in used_ids
        and item["transaction_type"] == "expense"
        # Surplus-allocation payments are extra principal, identified by the
        # origin note rather than category alone (categories are user-editable).
        and not (
            link_type == "debt"
            and item["category"] == "debt payment"
            and "extra debt snowball payment" in (item.get("notes") or "").lower()
        )
        and item["linked_obligation_type"] == link_type
        and item["linked_obligation_id"] == link_id
        and _within_window(date.fromisoformat(item["transaction_date"]), due_date, window_days)
    ]
    candidates.sort(
        key=lambda item: (
            abs((date.fromisoformat(item["transaction_date"]) - due_date).days),
            item["transaction_date"],
            item["id"],
        )
    )
    used_ids.update(item["id"] for item in candidates)
    return candidates


def _daily_cash_forecast(report: dict) -> list[dict]:
    """Project cash by date, conservatively applying outflows before inflows."""
    start = date.fromisoformat(report["start_date"])
    end = date.fromisoformat(report["end_date"])
    events: dict[date, dict[str, int]] = {}

    def add(day_value, key: str, amount: int) -> None:
        if not day_value or amount <= 0:
            return
        day = date.fromisoformat(day_value) if isinstance(day_value, str) else day_value
        bucket = events.setdefault(day, {"income_cents": 0, "expense_cents": 0})
        bucket[key] += amount

    for item in report.get("expected_income", []):
        add(item.get("date"), "income_cents", item.get("amount_cents", 0))
    for item in report.get("transactions", []):
        if item.get("type") == "income":
            add(item.get("date"), "income_cents", abs(item.get("amount_cents", 0)))
        elif item.get("type") == "expense":
            add(item.get("date"), "expense_cents", abs(item.get("amount_cents", 0)))
    for item in report.get("bills", []):
        add(item.get("due_date"), "expense_cents", item.get("amount_cents", 0))
    for item in report.get("minimum_debt_payments", []):
        add(item.get("due_date"), "expense_cents", item.get("amount_cents", 0))
    add(end, "expense_cents", report.get("funded_savings_cents", 0))

    balance = report.get("starting_cash_cents", 0)
    daily = []
    cursor = start
    while cursor <= end:
        bucket = events.get(cursor, {"income_cents": 0, "expense_cents": 0})
        opening = balance
        after_outflows = opening - bucket["expense_cents"]
        ending = after_outflows + bucket["income_cents"]
        lowest = min(opening, after_outflows, ending)
        daily.append({
            "date": cursor.isoformat(),
            "opening_cash_cents": opening,
            "income_cents": bucket["income_cents"],
            "outflows_cents": bucket["expense_cents"],
            "lowest_cash_cents": lowest,
            "ending_cash_cents": ending,
            "projected_shortfall_cents": max(0, -lowest),
        })
        balance = ending
        cursor += timedelta(days=1)
    return daily



@router.get("/pay-periods", response_class=HTMLResponse)
def pay_period_report_page():
    """Printable three-month report for the rolling pay-period forecast."""
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Three-Month Pay-Period Forecast · CashFlow Engine</title>
<style>
:root{font-family:system-ui,sans-serif;color:#17212b;background:#f3f6fa}*{box-sizing:border-box}
body{margin:0;padding:24px 14px 48px}main{max-width:1180px;margin:auto}
header{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:18px}
h1{font-size:1.65rem;letter-spacing:-.035em;margin:0;color:#155e75}h2{font-size:1.12rem;margin:0 0 12px}
p{line-height:1.5}.muted{color:#64748b}.actions{display:flex;gap:8px;flex-wrap:wrap}
button,.button{border:0;border-radius:8px;padding:10px 14px;background:#0e7490;color:#fff;font:inherit;font-weight:650;cursor:pointer;text-decoration:none}
button.secondary{background:#e2e8f0;color:#17212b}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:10px;margin:18px 0}
.metric,.panel,.period{background:#fff;border:1px solid #e2e8f0;border-radius:13px;padding:16px;box-shadow:0 4px 18px #172b4d08}
.metric span{display:block;color:#64748b;font-size:.88rem}.metric strong{display:block;font-size:1.35rem;color:#155e75;margin-top:4px}
#status{padding:12px;border-radius:9px;background:#e0f2fe;color:#075985;margin:12px 0}#status.error{background:#fee2e2;color:#991b1b}
#warnings{margin:12px 0;padding:0;list-style:none}#warnings li{padding:10px 12px;margin:6px 0;border-left:4px solid #d97706;background:#fff7ed;border-radius:6px}
.periods{display:grid;gap:14px}.period{break-inside:avoid;page-break-inside:avoid}
.period-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}
.period-title{font-weight:750;font-size:1.05rem}.tag{font-size:.8rem;font-weight:700;padding:4px 8px;border-radius:99px;background:#e2e8f0}
.tag.good{background:#dcfce7;color:#166534}.tag.warn{background:#fef3c7;color:#92400e}.tag.bad{background:#fee2e2;color:#991b1b}
.period-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin:12px 0}
.period-grid div{padding:9px;background:#f8fafc;border-radius:8px}.period-grid span{display:block;font-size:.78rem;color:#64748b}.period-grid strong{font-size:.98rem}
.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.88rem}th,td{text-align:right;padding:9px 8px;border-top:1px solid #e2e8f0;white-space:nowrap}th:first-child,td:first-child{text-align:left}thead th{color:#64748b;font-weight:650;border-top:0}
details{margin-top:10px}summary{cursor:pointer;font-weight:650;color:#0e7490}ul.detail-list{padding-left:20px;margin:8px 0}ul.detail-list li{margin:4px 0}
.footer-note{margin-top:18px;font-size:.86rem;color:#64748b}
.occurrence-actions{display:inline-flex;gap:5px;flex-wrap:wrap;margin-left:8px}
.occurrence-actions button{font-size:.75rem;padding:4px 7px;border-radius:6px}
.occurrence-actions .cancel{background:#9a3412}.occurrence-actions .reset{background:#475569}
@media(max-width:560px){body{padding:16px 10px 32px}h1{font-size:1.35rem}.metric,.panel,.period{padding:13px}}
@media print{
 @page{margin:12mm}body{padding:0;background:#fff;color:#111;font-size:10pt}
 header .actions,#status,.occurrence-actions{display:none!important}.metric,.panel,.period{box-shadow:none;border:1px solid #cbd5e1}
 .period{break-inside:avoid;page-break-inside:avoid;margin-bottom:8mm}.metrics{grid-template-columns:repeat(5,1fr);gap:5px}
 .metric{padding:8px}.metric strong{font-size:12pt}.period-grid{grid-template-columns:repeat(4,1fr)}
 details:not([open])>*:not(summary){display:block!important}summary{display:none}
}
</style></head><body><main>
<header><div><h1>Three-Month Pay-Period Forecast</h1><p class="muted" id="generated">Preparing report…</p></div>
<div class="actions"><button class="secondary" id="refresh" type="button">Refresh forecast</button><button id="print" type="button">Print / Save PDF</button></div></header>
<p class="muted">Use the bill controls in each period to change or skip one due date only. To change the recurring amount for all future occurrences, edit the bill in <a href="/bills">Bills</a>.</p>
<div id="status" role="status" aria-live="polite">Loading saved household schedules and transactions…</div>
<section class="metrics" id="metrics"></section>
<ul id="warnings"></ul>
<section class="panel"><h2>Pay-period detail</h2><div class="table-wrap"><table><thead><tr>
<th>Period</th><th>Income</th><th>Bills</th><th>Savings</th><th>Debt</th><th>Other expenses</th><th>Projected end</th><th>Carry above target</th><th>Status</th>
</tr></thead><tbody id="summary-rows"></tbody></table></div>
<p class="footer-note">The end-of-period target is a goal, not reserved money. Reserved cash includes scheduled bills, savings contributions, and planned debt payments. Forecast interest changes projected debt balances only; it is not recorded as a transaction.</p></section>
<section class="periods" id="periods"></section>
</main>
<script>
(function(){
 const $=id=>document.getElementById(id);
 const dollars=cents=>new Intl.NumberFormat(undefined,{style:"currency",currency:"USD"}).format((Number(cents)||0)/100);
 const escapeHtml=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
 const dateLabel=value=>{if(!value)return "—";const [y,m,d]=value.split("-").map(Number);return new Date(y,m-1,d).toLocaleDateString(undefined,{month:"short",day:"numeric",year:"numeric"});};
 function statusFor(p){if(p.projected_ending_cash_cents<0)return ["Negative cash","bad"];if(p.target_shortfall_cents>0)return ["Carry-over needed","warn"];if(p.below_target_cents>0)return ["Below target","warn"];return ["On track","good"];}
 function list(items,render){return items&&items.length?"<ul class='detail-list'>"+items.map(render).join("")+"</ul>":"<p class='muted'>None scheduled.</p>";}
 function render(report){
  $("generated").textContent="Generated "+dateLabel(report.generated_on)+" · Forecast through "+dateLabel(report.horizon_end)+" · Current cash after recorded activity";
  const metrics=[
   ["Current cash",report.current_cash_cents],
   ["Reserved cash",report.reserved_cash_cents],
   ["Maximum spendable",report.max_spendable_cents],
   ["Spendable while keeping target",report.spendable_while_keeping_target_cents],
   ["End-of-period target",report.end_of_period_target_cents]
  ];
  $("metrics").innerHTML=metrics.map(([label,value])=>"<div class='metric'><span>"+escapeHtml(label)+"</span><strong>"+dollars(value)+"</strong></div>").join("");
  $("warnings").innerHTML=(report.warnings||[]).map(w=>"<li>"+escapeHtml(w.type==="bill_underfunded"?"Bill "+w.name+" due "+dateLabel(w.start_date)+" is underfunded by "+dollars(w.shortfall_cents):w.type==="negative_cash"?"Projected negative cash in the period starting "+dateLabel(w.start_date):"The required carry-over is short by "+dollars(w.shortfall_cents)+" in the period starting "+dateLabel(w.start_date))+"</li>").join("");
  $("summary-rows").innerHTML=(report.periods||[]).map(p=>{
   const [label]=statusFor(p);
   return "<tr><td>"+dateLabel(p.start_date)+" – "+dateLabel(p.end_date)+(p.is_current_period?" · Current":"")+"</td><td>"+dollars(p.income_cents)+"</td><td>"+dollars(p.bills_cents)+"</td><td>"+dollars(p.savings_cents)+"</td><td>"+dollars(p.debt_payments_cents)+"</td><td>"+dollars(p.other_expenses_cents)+"</td><td>"+dollars(p.projected_ending_cash_cents)+"</td><td>"+dollars(p.carry_out_above_target_cents)+"</td><td>"+escapeHtml(label)+"</td></tr>";
  }).join("");
  $("periods").innerHTML=(report.periods||[]).map(p=>{
   const [label,kind]=statusFor(p);
   const paydays=(p.paycheck_dates||[]).map(dateLabel).join(", ")||"No paycheck dates";
   const bills=list(p.bills,x=>{
    const label=x.cancelled?" · cancelled":(x.is_overridden?" · one-off override":"");
    const reset=x.is_overridden?"<button type='button' class='reset bill-override-reset' data-bill-id='"+x.bill_id+"' data-due-date='"+x.due_date+"'>Restore</button>":"";
    const controls=x.cancelled?reset:"<button type='button' class='bill-override-edit' data-bill-id='"+x.bill_id+"' data-due-date='"+x.due_date+"' data-amount='"+x.amount_cents+"'>Edit this date</button><button type='button' class='cancel bill-override-cancel' data-bill-id='"+x.bill_id+"' data-due-date='"+x.due_date+"'>Skip this date</button>"+reset;
    return "<li>"+escapeHtml(x.name)+" · "+dateLabel(x.due_date)+" · "+dollars(x.amount_cents)+escapeHtml(label)+(x.autopay?" · scheduled autopay":"")+" <span class='occurrence-actions'>"+controls+"</span></li>";
   });
   const savings=list(p.savings,x=>"<li>"+escapeHtml(x.name)+" · "+dollars(x.amount_cents)+"</li>");
   const debt=list(p.debt_events,x=>"<li>"+escapeHtml(x.debt_name)+" · "+dateLabel(x.date)+" · "+escapeHtml(x.type)+(x.actual?" (recorded)":" (forecast)")+" · "+dollars(x.amount_cents)+"</li>");
   const income=list(p.income_items,x=>"<li>"+escapeHtml(x.name)+" · "+dateLabel(x.date)+" · "+dollars(x.amount_cents)+"</li>");
   const transactions=list(p.transactions,x=>"<li>"+dateLabel(x.date)+" · "+escapeHtml(x.description)+" · "+dollars(x.amount_cents)+" · "+escapeHtml(x.type)+"</li>");
   return "<article class='period'><div class='period-head'><div><div class='period-title'>"+dateLabel(p.start_date)+" – "+dateLabel(p.end_date)+(p.is_current_period?" · Current period":"")+"</div><div class='muted'>Paydays: "+escapeHtml(paydays)+"</div></div><span class='tag "+kind+"'>"+escapeHtml(label)+"</span></div>"+
    "<div class='period-grid'><div><span>Cash carried in</span><strong>"+dollars(p.starting_cash_cents)+"</strong></div><div><span>Income</span><strong>"+dollars(p.income_cents)+"</strong></div><div><span>Bills</span><strong>"+dollars(p.bills_cents)+"</strong></div><div><span>Savings</span><strong>"+dollars(p.savings_cents)+"</strong></div><div><span>Debt payments</span><strong>"+dollars(p.debt_payments_cents)+"</strong></div><div><span>Other expenses</span><strong>"+dollars(p.other_expenses_cents)+"</strong></div><div><span>Projected ending cash</span><strong>"+dollars(p.projected_ending_cash_cents)+"</strong></div><div><span>Carry above target</span><strong>"+dollars(p.carry_out_above_target_cents)+"</strong></div></div>"+
    "<details><summary>Scheduled income</summary>"+income+"</details><details><summary>Bills</summary>"+bills+"</details><details><summary>Savings contributions</summary>"+savings+"</details><details><summary>Debt interest and payments</summary>"+debt+"</details><details><summary>Recorded future transactions</summary>"+transactions+"</details></article>";
  }).join("");
  document.querySelectorAll(".bill-override-edit").forEach(button=>button.addEventListener("click",async()=>{
   const amount=prompt("New amount in dollars for "+button.dataset.dueDate+" only:",(Number(button.dataset.amount)/100).toFixed(2));
   if(amount===null)return;
   const parsed=Number(amount);
   if(!Number.isFinite(parsed)||parsed<0){alert("Enter a valid non-negative amount.");return;}
   try{
    const response=await fetch("/api/bills/"+button.dataset.billId+"/occurrences/"+button.dataset.dueDate,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({amount_cents:Math.round(parsed*100),cancelled:false})});
    const data=await response.json();if(!response.ok)throw new Error(data.detail||"Could not update bill occurrence.");await load();
   }catch(error){alert(error.message);}
  }));
  document.querySelectorAll(".bill-override-cancel").forEach(button=>button.addEventListener("click",async()=>{
   if(!confirm("Skip this bill occurrence on "+button.dataset.dueDate+"? The recurring schedule will remain unchanged."))return;
   try{
    const response=await fetch("/api/bills/"+button.dataset.billId+"/occurrences/"+button.dataset.dueDate,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({cancelled:true})});
    const data=await response.json();if(!response.ok)throw new Error(data.detail||"Could not cancel bill occurrence.");await load();
   }catch(error){alert(error.message);}
  }));
  document.querySelectorAll(".bill-override-reset").forEach(button=>button.addEventListener("click",async()=>{
   if(!confirm("Restore the recurring amount for "+button.dataset.dueDate+"?"))return;
   try{
    const response=await fetch("/api/bills/"+button.dataset.billId+"/occurrences/"+button.dataset.dueDate,{method:"DELETE"});
    const data=await response.json();if(!response.ok)throw new Error(data.detail||"Could not restore bill occurrence.");await load();
   }catch(error){alert(error.message);}
  }));
  const posted=report.auto_posted;
  const postedNote=posted&&(posted.posted_paychecks||posted.posted_bills)?" Automatically recorded "+posted.posted_paychecks+" paycheck(s) and "+posted.posted_bills+" bill payment(s) in the internal ledger.":"";
  $("status").textContent=(report.target_maintainable?"Forecast loaded. The end-of-period target is maintainable across the forecast.":"Forecast loaded. Review the carry-over and cash warnings below.")+postedNote;
  $("status").classList.toggle("error",!report.target_maintainable);
 }
 async function load(){
  $("status").textContent="Loading saved household schedules and transactions…";
  $("status").classList.remove("error");
  try{const response=await fetch("/api/cash-flow/pay-periods");const data=await response.json();if(!response.ok)throw new Error(data.detail||"Forecast request failed");render(data);}
  catch(error){$("status").textContent="Forecast unavailable: "+error.message;$("status").classList.add("error");}
 }
 $("refresh").addEventListener("click",load);
 $("print").addEventListener("click",()=>window.print());
 load();
})();
</script></body></html>"""


@router.get("/api/cash-flow/pay-periods")
def pay_period_cash_flow(as_of_date: date | None = Query(default=None)):
    """Build the rolling three-calendar-month forecast from saved household records."""
    try:
        with database.connect() as db:
            auto_posted = None
            if as_of_date is None:
                auto_posted = post_due_scheduled_transactions(db)
            report = build_pay_period_forecast(db, as_of_date)
            if auto_posted is not None:
                report["auto_posted"] = auto_posted
            return report
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/cash-flow/weekly")
def weekly_cash_flow(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    forecast_weeks: int = 13,
    estimate_interest: bool = Query(default=False),
):
    """Forecast the selected window and reserve cash for later weeks' obligations."""
    if start_date is None:
        with database.connect() as db:
            settings = db.execute(
                "SELECT financial_week_start FROM household_settings WHERE id = 1"
            ).fetchone()
        weekday_name = settings["financial_week_start"] if settings else "Friday"
        weekday_index = {
            "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
            "Friday": 4, "Saturday": 5, "Sunday": 6,
        }.get(weekday_name, 4)
        today = date.today()
        start = today - timedelta(days=(today.weekday() - weekday_index) % 7)
    else:
        start = start_date
    end = end_date or (start + timedelta(days=6))
    if end < start:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    if (end - start).days > 30:
        raise HTTPException(status_code=422, detail="Cash-flow forecast window cannot exceed 31 days")
    if isinstance(forecast_weeks, bool) or not isinstance(forecast_weeks, int) or not 1 <= forecast_weeks <= 52:
        raise HTTPException(status_code=422, detail="forecast_weeks must be between 1 and 52")

    report = _weekly_cash_flow_for_window(start, end)
    current_ending_cash = (
        report["projected_cash_before_buffer_cents"] - report["funded_savings_cents"]
    )
    current_daily_forecast = _daily_cash_forecast(report)
    current_daily_low = min((item["lowest_cash_cents"] for item in current_daily_forecast), default=current_ending_cash)
    lowest_projected_cash = current_daily_low
    future_forecast = []
    carried_cash = current_ending_cash

    # Optional interest affects explanatory projected debt balances only.
    # It never changes cash, transactions, or saved balances. APR is prorated
    # daily across each future window and rounded to the nearest cent (half up).
    with database.connect() as db:
        debt_rows = db.execute(
            """SELECT id, name, balance_cents, apr_percent
               FROM credit_cards WHERE active = 1 ORDER BY id"""
        ).fetchall()
    projected_debts = {
        row["id"]: {
            "name": row["name"],
            "balance_cents": row["balance_cents"],
            "apr_basis_points": max(0, int(float(row["apr_percent"]) * 100 + 0.5)),
            "apr_percent": float(row["apr_percent"]),
        }
        for row in debt_rows
    }
    for payment in report["minimum_debt_payments"]:
        debt = projected_debts.get(payment["id"])
        if debt:
            debt["balance_cents"] = max(
                0, debt["balance_cents"]
                - payment["qualifying_payments_cents"] - payment["amount_cents"]
            )
    total_estimated_interest_cents = 0
    # Carry only arrears that already exist at the end of the selected week.
    # Future target shortfalls remain forecast gaps; they do not become new
    # arrears until those weeks actually occur.
    carried_savings_arrears = {
        item["goal_id"]: item["arrears_after_recorded_contributions_cents"]
        for item in report["savings_plan"]
    }
    # Arrears remain visible until an actual contribution is recorded, but
    # catch-up cash already reserved in an earlier projected week must not be
    # reserved a second time.
    carried_savings_catch_up = {
        item["goal_id"]: max(
            0, item["catch_up_cents"] - item["funded_catch_up_cents"]
        )
        for item in report["savings_plan"]
    }
    future_start = end + timedelta(days=1)

    # Carry each period's ending cash forward. Re-read schedules and transactions
    # for every future window so each request reflects current data.
    for offset in range(1, forecast_weeks):
        future_end = future_start + timedelta(days=6)
        future = _weekly_cash_flow_for_window(
            future_start, future_end, starting_cash_override=carried_cash,
            savings_arrears_override=carried_savings_arrears,
            savings_catch_up_override=carried_savings_catch_up,
        )
        estimated_debt_interest = []
        weekly_estimated_interest_cents = 0
        if estimate_interest:
            days_in_period = (future_end - future_start).days + 1
            denominator = 10_000 * 365
            for debt_id, debt in projected_debts.items():
                opening_balance = debt["balance_cents"]
                if opening_balance <= 0 or debt["apr_basis_points"] <= 0:
                    interest_cents = 0
                else:
                    numerator = opening_balance * debt["apr_basis_points"] * days_in_period
                    interest_cents = (numerator + denominator // 2) // denominator
                estimated_debt_interest.append({
                    "debt_id": debt_id,
                    "debt_name": debt["name"],
                    "opening_balance_cents": opening_balance,
                    "apr_percent": debt["apr_percent"],
                    "estimated_interest_cents": interest_cents,
                })
                weekly_estimated_interest_cents += interest_cents
        ending_cash = (
            future["projected_cash_before_buffer_cents"] - future["funded_savings_cents"]
        )
        future_daily_forecast = _daily_cash_forecast(future)
        future_daily_low = min((item["lowest_cash_cents"] for item in future_daily_forecast), default=ending_cash)
        lowest_projected_cash = min(lowest_projected_cash, future_daily_low)
        if estimate_interest:
            payments_by_debt = {
                payment["id"]: payment["qualifying_payments_cents"] + payment["amount_cents"]
                for payment in future["minimum_debt_payments"]
            }
            for debt_id, debt in projected_debts.items():
                interest = next(
                    (item["estimated_interest_cents"] for item in estimated_debt_interest
                     if item["debt_id"] == debt_id),
                    0,
                )
                reduction = min(
                    debt["balance_cents"] + interest,
                    max(0, payments_by_debt.get(debt_id, 0)),
                )
                debt["balance_cents"] = max(0, debt["balance_cents"] + interest - reduction)
            total_estimated_interest_cents += weekly_estimated_interest_cents

        future_forecast.append({
            "start_date": future_start.isoformat(),
            "end_date": future_end.isoformat(),
            "starting_cash_cents": carried_cash,
            "expected_income_cents": future["expected_income_cents"],
            "recorded_income_cents": future["recorded_income_cents"],
            "bills_due_cents": future["bills_due_cents"],
            "minimum_debt_payments_cents": future["minimum_debt_payments_cents"],
            "transaction_expenses_cents": future["transaction_expenses_cents"],
            "planned_savings_cents": future["planned_savings_cents"],
            "funded_savings_cents": future["funded_savings_cents"],
            "savings_shortfall_cents": future["savings_shortfall_cents"],
            "savings_arrears_cents": sum(
                item["arrears_cents"] for item in future["savings_plan"]
            ),
            "projected_ending_cash_cents": ending_cash,
            "projected_shortfall_cents": max(0, -future_daily_low),
            "buffer_shortfall_cents": 0,
            "lowest_projected_cash_cents": future_daily_low,
            "daily_forecast": future_daily_forecast,
            "estimated_debt_interest_cents": weekly_estimated_interest_cents,
            "estimated_debt_interest": estimated_debt_interest,
            "projected_debt_balances": (
                [
                    {
                        "debt_id": debt_id,
                        "debt_name": debt["name"],
                        "projected_balance_cents": debt["balance_cents"],
                    }
                    for debt_id, debt in projected_debts.items()
                ] if estimate_interest else []
            ),
        })
        carried_cash = ending_cash
        carried_savings_arrears = {
            item["goal_id"]: item["arrears_after_recorded_contributions_cents"]
            for item in future["savings_plan"]
        }
        carried_savings_catch_up = {
            item["goal_id"]: max(
                0, item["catch_up_cents"] - item["funded_catch_up_cents"]
            )
            for item in future["savings_plan"]
        }
        future_start = future_end + timedelta(days=1)

    # Today's daily low is the current-period spending ceiling. Future weeks
    # can reserve part of that amount when their own daily low is lower.
    future_daily_lows = [
        item["lowest_projected_cash_cents"] for item in future_forecast
    ]
    future_lowest_cash = min(future_daily_lows, default=current_daily_low)
    safe_before_future_reserve = max(0, current_daily_low)
    safe_to_spend = max(0, min(current_daily_low, future_lowest_cash))
    future_obligation_reservation = max(
        0, safe_before_future_reserve - max(0, future_lowest_cash)
    )
    report["projected_ending_cash_cents"] = current_ending_cash
    report["daily_forecast"] = current_daily_forecast
    report["free_to_spend_before_future_reserve_cents"] = safe_before_future_reserve
    report["future_obligation_reservation_cents"] = future_obligation_reservation
    # Legacy response alias retained for clients upgrading from the buffer model.
    report["future_buffer_reservation_cents"] = future_obligation_reservation
    # The legacy surplus-allocation workflow is retired. Old planned rows remain
    # in SQLite for history, but no longer reserve cash or reduce forecast capacity.
    report["free_to_spend_cents"] = safe_to_spend
    report["extra_debt_payoff_capacity_cents"] = safe_to_spend
    report["forecast_weeks"] = forecast_weeks
    report["estimated_interest_enabled"] = estimate_interest
    report["estimated_debt_interest_cents"] = total_estimated_interest_cents
    report["lowest_projected_cash_cents"] = lowest_projected_cash
    report["future_buffer_shortfall_cents"] = 0
    report["horizon_buffer_replenishment_cents"] = 0
    report["projected_shortfall_cents"] = max(
        report["projected_shortfall_cents"],
        max((item["projected_shortfall_cents"] for item in current_daily_forecast), default=0),
        max((item["projected_shortfall_cents"] for item in future_forecast), default=0),
    )
    report["buffer_shortfall_cents"] = 0
    report["buffer_replenishment_cents"] = 0
    report["buffer_target_cents"] = 0
    report["future_forecast"] = future_forecast
    report["notes"].append(
        "Safe-to-spend is the lowest projected daily cash balance across "
        + str(forecast_weeks) + " forecast week(s), after scheduled income, bills, "
        "minimum debt payments, recorded transactions, and planned savings. Same-day "
        "outflows are tested before inflows because exact posting times are unknown. "
        "There is no mandatory household buffer; savings goals remain separate commitments."
    )
    report["notes"].append(
        "Daily checkpoints expose an earlier-in-the-week shortfall that a weekly net "
        "total could hide. A negative daily checkpoint means the schedule requires more "
        "cash before later income arrives."
    )
    if estimate_interest:
        report["notes"].append(
            "Estimated debt interest is an optional projection using each active debt's "
            "opening projected balance, APR, and a daily-prorated calculation rounded to cents. "
            "Interest is estimated before that week's projected payments reduce the balance. "
            "It does not change cash-flow totals, saved debt balances, or recorded transactions. "
            "Actual lender interest may differ due to daily balances, statement cycles, payment "
            "timing, fees, and lender-specific terms."
        )
    else:
        report["notes"].append(
            "Estimated debt interest is off. Enable the optional estimate to see projected "
            "interest and balances; estimates never alter recorded transactions or saved balances."
        )
    report["notes"].append(
        "Savings arrears already present in the selected week carry into future weeks. "
        "Each future week adds its regular savings target, but an unfunded future target "
        "is not counted as a new arrears balance until that week occurs. Recorded "
        "contributions reduce arrears; planned or funded amounts are not treated as completed transfers."
    )
    return report


def _weekly_cash_flow_for_window(
    start: date,
    end: date,
    starting_cash_override: int | None = None,
    savings_arrears_override: dict[int, int] | None = None,
    savings_catch_up_override: dict[int, int] | None = None,
) -> dict:
    """Calculate one period, optionally carrying known savings arrears and cash."""
    with database.connect() as db:
        settings = db.execute(
            "SELECT starting_cash_cents, financial_week_start FROM household_settings WHERE id = 1"
        ).fetchone()
        snapshot = db.execute(
            """SELECT effective_week_start, balance_cents, confirmed_at
               FROM cash_balance_snapshots
               WHERE effective_week_start <= ?
               ORDER BY effective_week_start DESC, id DESC LIMIT 1""",
            (start.isoformat(),),
        ).fetchone()
        any_snapshot = db.execute(
            "SELECT 1 FROM cash_balance_snapshots LIMIT 1"
        ).fetchone()
        starting_cash = (
            starting_cash_override
            if starting_cash_override is not None
            else (
                snapshot["balance_cents"] if snapshot
                else (0 if any_snapshot else (settings["starting_cash_cents"] if settings else 0))
            )
        )
        starting_cash_effective_week = snapshot["effective_week_start"] if snapshot else None
        starting_cash_confirmed_at = snapshot["confirmed_at"] if snapshot else None
        starting_cash_is_stale = True
        if snapshot:
            try:
                starting_cash_is_stale = (
                    date.today() - date.fromisoformat(snapshot["confirmed_at"][:10])
                ).days > CASH_SNAPSHOT_STALE_DAYS
            except (TypeError, ValueError):
                starting_cash_is_stale = True
        # Retain the legacy database column for upgrade compatibility; it no longer
        # constrains safe-to-spend or savings funding.
        buffer_target = 0

        # Include a short look-back for payments recorded just before the window
        # so they can mark a scheduled obligation as already covered.
        match_start = start - timedelta(days=31)
        transaction_rows = db.execute(
            """SELECT id, transaction_date, transaction_type, category, description, notes,
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
                      i.end_date, p.name AS person_name
               FROM income_sources i JOIN persons p ON p.id = i.person_id
               WHERE i.active = 1 AND p.active = 1
               ORDER BY i.next_payday, i.id"""
        ).fetchall()
        override_rows = db.execute(
            """SELECT income_source_id, start_date, end_date, amount_cents
               FROM income_amount_overrides
               WHERE start_date <= ? AND end_date >= ?
               ORDER BY start_date, id""",
            (end.isoformat(), start.isoformat()),
        ).fetchall()
        overrides_by_source = {}
        for override in override_rows:
            overrides_by_source.setdefault(override["income_source_id"], []).append(override)

        for row in rows:
            anchor = date.fromisoformat(row["next_payday"])
            last_payday = date.fromisoformat(row["end_date"]) if row["end_date"] else None
            for payday in occurrences_between(anchor, row["frequency"], start, end):
                if last_payday is not None and payday > last_payday:
                    continue
                amount_cents = row["net_amount_cents"]
                for override in overrides_by_source.get(row["id"], []):
                    if override["start_date"] <= payday.isoformat() <= override["end_date"]:
                        amount_cents = override["amount_cents"]
                        break
                income_occurrences.append({
                    "source_id": row["id"],
                    "name": f'{row["person_name"]} — {row["name"]}',
                    "date": payday.isoformat(),
                    "amount_cents": amount_cents,
                })
        income_occurrences.sort(key=lambda item: (item["date"], item["name"]))
        income_schedules = {
            row["id"]: (date.fromisoformat(row["next_payday"]), row["frequency"])
            for row in rows
        }
        used_transactions: set[int] = set()
        expected_income = []
        covered_income = []
        for item in income_occurrences:
            due_date = date.fromisoformat(item["date"])
            anchor, frequency = income_schedules[item["source_id"]]

            # A linked paycheck may be recorded up to seven days early or late,
            # but it must cover the closest scheduled occurrence for that source.
            # Otherwise a paycheck from last week could suppress this week's
            # expected income when the paydays are exactly seven days apart.
            eligible_transactions = []
            for transaction in all_transactions:
                if (
                    transaction["linked_obligation_type"] != "income"
                    or transaction["linked_obligation_id"] != item["source_id"]
                    or transaction["id"] in used_transactions
                ):
                    continue
                transaction_date = date.fromisoformat(transaction["transaction_date"])
                if not _within_window(transaction_date, due_date, 7):
                    continue
                nearby_paydays = occurrences_between(
                    anchor, frequency,
                    transaction_date - timedelta(days=7),
                    transaction_date + timedelta(days=7),
                )
                if nearby_paydays:
                    nearest_payday = min(
                        nearby_paydays,
                        key=lambda payday: (abs((payday - transaction_date).days), payday),
                    )
                    if nearest_payday == due_date:
                        eligible_transactions.append(transaction)

            match = _take_matching_transaction(
                eligible_transactions, used_transactions, "income", item["source_id"],
                due_date, 7,
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
            match_window = max(7, row["flexible_window_days"] or 0)
            # Consume earlier occurrences first so a payment exactly seven days
            # before a weekly due date cannot suppress the following occurrence.
            occurrence_start = start - timedelta(days=match_window + 7)
            for due in occurrences_between(anchor, row["frequency"], occurrence_start, end):
                # A transaction can fall inside two adjacent weekly windows.
                # Assign it only to its nearest scheduled occurrence, with ties
                # going to the earlier due date, so a payment on this Friday is
                # not also consumed by next Friday's bill.
                eligible_transactions = []
                for transaction in all_transactions:
                    if (
                        transaction["id"] in used_transactions
                        or transaction["transaction_type"] != "expense"
                        or transaction["linked_obligation_type"] != "bill"
                        or transaction["linked_obligation_id"] != row["id"]
                    ):
                        continue
                    transaction_date = date.fromisoformat(transaction["transaction_date"])
                    if not _within_window(transaction_date, due, match_window):
                        continue
                    nearby_due_dates = occurrences_between(
                        anchor, row["frequency"],
                        transaction_date - timedelta(days=match_window),
                        transaction_date + timedelta(days=match_window),
                    )
                    if nearby_due_dates:
                        nearest_due = min(
                            nearby_due_dates,
                            key=lambda candidate: (
                                abs((candidate - transaction_date).days), candidate,
                            ),
                        )
                        if nearest_due == due:
                            eligible_transactions.append(transaction)
                matches = _take_matching_transactions(
                    eligible_transactions, used_transactions, "bill", row["id"], due,
                    match_window,
                )
                paid_cents = sum(abs(item["amount_cents"]) for item in matches)
                covered_cents = min(row["amount_cents"], paid_cents)
                remaining_cents = max(0, row["amount_cents"] - covered_cents)
                if due < start:
                    continue
                bills.append({
                    "id": row["id"], "name": row["name"], "category": row["category"],
                    "due_date": due.isoformat(), "amount_cents": remaining_cents,
                    "scheduled_amount_cents": row["amount_cents"],
                    "covered_amount_cents": covered_cents,
                    "status": (
                        "recorded" if remaining_cents == 0 and covered_cents > 0
                        else "partial" if covered_cents > 0
                        else "due"
                    ),
                    "covered_by_transaction_id": matches[0]["id"] if len(matches) == 1 else None,
                    "covered_by_transaction_ids": [item["id"] for item in matches],
                    "transaction_date": matches[0]["transaction_date"] if len(matches) == 1 else None,
                    "transaction_dates": [item["transaction_date"] for item in matches],
                })
        bills.sort(key=lambda item: (item["due_date"], item["name"]))

        debts = []
        rows = db.execute(
            """SELECT id, name, balance_cents, minimum_payment_cents,
                      next_due_date, due_day_of_month
               FROM credit_cards WHERE active = 1 ORDER BY id"""
        ).fetchall()
        for row in rows:
            anchor = _date(row["next_due_date"])
            due_dates = (
                occurrences_between(anchor, "monthly", start, end)
                if anchor else _monthly_due_dates(row["due_day_of_month"], start, end)
            )
            capped_minimum = min(row["minimum_payment_cents"], row["balance_cents"])
            for due in due_dates:
                matches = _take_matching_transactions(
                    all_transactions, used_transactions, "debt", row["id"], due, 7,
                )
                qualifying_paid = sum(abs(item["amount_cents"]) for item in matches)
                paid_toward_minimum = min(capped_minimum, qualifying_paid)
                remaining_minimum = max(0, capped_minimum - paid_toward_minimum)
                debts.append({
                    "id": row["id"], "name": row["name"], "due_date": due.isoformat(),
                    "original_minimum_payment_cents": row["minimum_payment_cents"],
                    "balance_capped_minimum_cents": capped_minimum,
                    "qualifying_payments_cents": qualifying_paid,
                    "paid_toward_minimum_cents": paid_toward_minimum,
                    "extra_payment_cents": max(0, qualifying_paid - capped_minimum),
                    "amount_cents": remaining_minimum,
                    "status": (
                        "recorded" if remaining_minimum == 0 and qualifying_paid > 0
                        else "partial" if qualifying_paid > 0
                        else "due"
                    ),
                    "covered_by_transaction_ids": [item["id"] for item in matches],
                    "covered_by_transaction_id": matches[0]["id"] if matches else None,
                    "transaction_date": matches[0]["transaction_date"] if len(matches) == 1 else None,
                    "transaction_dates": [item["transaction_date"] for item in matches],
                })
        debts.sort(key=lambda item: (item["due_date"], item["name"]))

        goals = db.execute(
            """SELECT id, name, target_amount_cents, current_balance_cents, target_date,
                      weekly_target_cents, schedule_start_date, created_at, priority
               FROM savings_goals WHERE active = 1
               ORDER BY priority, name COLLATE NOCASE, id"""
        ).fetchall()
        savings_history = [
            dict(row) for row in db.execute(
                """SELECT a.savings_goal_id, t.transaction_date,
                          COALESCE(a.actual_amount_cents, 0) AS amount_cents
                   FROM surplus_allocations a
                   JOIN transactions t ON t.id = a.transaction_id
                   WHERE a.allocation_type = 'savings' AND a.status = 'recorded'
                     AND a.savings_goal_id IS NOT NULL
                   ORDER BY t.transaction_date, a.id"""
            ).fetchall()
        ]
        # New savings contributions are recorded directly in the transaction
        # ledger. Read their explicit goal marker so the retained legacy weekly
        # checklist/reminder calculation sees the same actual contribution.
        direct_contributions = db.execute(
            """SELECT transaction_date, notes, ABS(amount_cents) AS amount_cents
               FROM transactions
               WHERE transaction_type = 'expense'
                 AND notes LIKE 'cashflow_savings_goal_id=%;%'
               ORDER BY transaction_date, id"""
        ).fetchall()
        for contribution in direct_contributions:
            notes = contribution["notes"] or ""
            marker = "cashflow_savings_goal_id="
            try:
                goal_id = int(notes.split(marker, 1)[1].split(";", 1)[0])
            except (IndexError, ValueError):
                continue
            savings_history.append({
                "savings_goal_id": goal_id,
                "transaction_date": contribution["transaction_date"],
                "amount_cents": contribution["amount_cents"],
            })

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

    uncovered_bills = [item for item in bills if item["amount_cents"] > 0]
    uncovered_debts = [item for item in debts if item["amount_cents"] > 0]
    expected_income_cents = sum(item["amount_cents"] for item in expected_income)
    bills_due_cents = sum(item["amount_cents"] for item in uncovered_bills)
    minimum_debt_cents = sum(item["amount_cents"] for item in uncovered_debts)

    # Every active savings goal is budgeted per financial week, independently
    # of paydays. Dated goals derive their weekly target from the deadline;
    # undated goals use the user-entered weekly amount for ongoing savings.
    # Missed weekly targets carry forward, and recorded contributions reduce arrears.
    savings_plan = []
    planned_savings = 0
    history_by_goal: dict[int, list[tuple[date, int]]] = {}
    for entry in savings_history:
        history_by_goal.setdefault(entry["savings_goal_id"], []).append(
            (date.fromisoformat(entry["transaction_date"]), entry["amount_cents"])
        )
    for goal in goals:
        has_deadline = bool(goal["target_date"])
        remaining = max(0, goal["target_amount_cents"] - goal["current_balance_cents"])
        target_date = date.fromisoformat(goal["target_date"]) if has_deadline else None
        schedule_start = date.fromisoformat(goal["schedule_start_date"] or goal["created_at"][:10])
        weekly_target = goal["weekly_target_cents"] or 0
        if has_deadline and weekly_target == 0 and remaining > 0 and target_date >= start:
            weeks_left = max(1, ((target_date - start).days // 7) + 1)
            weekly_target = (remaining + weeks_left - 1) // weeks_left
        elapsed_weeks = max(0, (start - schedule_start).days // 7)
        history = history_by_goal.get(goal["id"], [])
        saved_before = sum(amount for day, amount in history if day < start)
        saved_this_week = sum(amount for day, amount in history if start <= day <= end)
        if savings_arrears_override is None:
            arrears = max(0, weekly_target * elapsed_weeks - saved_before)
            catch_up_due = max(0, arrears - saved_this_week)
        else:
            arrears = max(0, savings_arrears_override.get(goal["id"], 0))
            catch_up_due = max(
                0, (savings_catch_up_override or {}).get(goal["id"], 0)
            )
            catch_up_due = min(catch_up_due, max(0, arrears - saved_this_week))

        # A goal with a date stops reserving new weekly deposits after its
        # deadline. An undated goal with a positive amount stops once funded;
        # amount=0 means ongoing general savings with no finish line.
        if has_deadline and target_date < start:
            due_this_week = 0
        else:
            # The outstanding arrears remain visible, but catch-up already
            # reserved in an earlier forecast week is not reserved again.
            # Recorded contributions pay arrears first; only any excess reduces
            # this week's regular target.
            due_this_week = max(
                0, weekly_target + catch_up_due - max(0, saved_this_week - arrears)
            )
            if goal["target_amount_cents"] > 0:
                due_this_week = min(remaining, due_this_week)

        if has_deadline:
            weeks_remaining = max(1, ((target_date - start).days // 7) + 1) if target_date >= start else 0
            required_weekly = (remaining + weeks_remaining - 1) // weeks_remaining if weeks_remaining else remaining
            behind_schedule = bool(target_date < start or required_weekly > weekly_target)
        else:
            required_weekly = None
            behind_schedule = False

        catch_up_due = min(catch_up_due, due_this_week)
        savings_plan.append({
            "goal_id": goal["id"], "goal_name": goal["name"],
            "weekly_target_cents": weekly_target, "regular_target_cents": weekly_target,
            "arrears_cents": arrears, "outstanding_arrears_cents": arrears,
            "catch_up_cents": catch_up_due,
            "regular_target_due_cents": max(0, due_this_week - catch_up_due),
            "arrears_after_recorded_contributions_cents": max(0, arrears - saved_this_week),
            "planned_this_week_cents": due_this_week,
            "recorded_this_week_cents": saved_this_week,
            "remaining_cents": remaining if goal["target_amount_cents"] > 0 else None,
            "target_date": goal["target_date"],
            "required_weekly_to_deadline_cents": required_weekly,
            "behind_schedule": behind_schedule,
        })
        planned_savings += due_this_week

    projected_cash = (
        starting_cash + expected_income_cents + transaction_income
        - bills_due_cents - minimum_debt_cents - transaction_expenses
    )
    available_cash = max(0, projected_cash)
    funded_savings = min(planned_savings, available_cash)
    savings_shortfall = planned_savings - funded_savings
    remaining_savings_capacity = funded_savings
    for item in savings_plan:
        item["funded_this_week_cents"] = min(
            item["planned_this_week_cents"], remaining_savings_capacity
        )
        item["unfunded_this_week_cents"] = (
            item["planned_this_week_cents"] - item["funded_this_week_cents"]
        )
        item["funded_catch_up_cents"] = min(
            item["catch_up_cents"], item["funded_this_week_cents"]
        )
        remaining_savings_capacity -= item["funded_this_week_cents"]
        item["deadline_warning"] = bool(
            item["behind_schedule"] or item["unfunded_this_week_cents"] > 0
        )
    free_to_spend = max(0, available_cash - funded_savings)

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "financial_week_start": (settings["financial_week_start"] if settings and "financial_week_start" in settings.keys() else "Friday"),
        "starting_cash_cents": starting_cash,
        "starting_cash_effective_week": starting_cash_effective_week,
        "starting_cash_confirmed_at": starting_cash_confirmed_at,
        "starting_cash_is_stale": starting_cash_is_stale,
        "buffer_target_cents": buffer_target,
        "expected_income": expected_income,
        "expected_income_cents": expected_income_cents,
        "recorded_income_cents": transaction_income,
        "covered_income": covered_income,
        "bills": bills,
        "bills_due_cents": bills_due_cents,
        "bills_covered_by_transactions_cents": sum(
            item.get("covered_amount_cents", 0) for item in bills
        ),
        "minimum_debt_payments": debts,
        "minimum_debt_payments_cents": minimum_debt_cents,
        "debt_payments_covered_by_transactions_cents": sum(
            item["paid_toward_minimum_cents"] for item in debts
        ),
        "debt_extra_payments_by_transactions_cents": sum(
            item["extra_payment_cents"] for item in debts
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
        "savings_plan": savings_plan,
        "funded_savings_cents": funded_savings,
        "savings_shortfall_cents": savings_shortfall,
        "projected_cash_before_buffer_cents": projected_cash,
        "buffer_replenishment_cents": 0,
        "projected_shortfall_cents": max(0, -projected_cash),
        "buffer_shortfall_cents": 0,
        "free_to_spend_cents": free_to_spend,
        "extra_debt_payoff_capacity_cents": free_to_spend,
        "notes": [
            "Savings arrears are based on missed targets before the selected week and recorded contributions; the selected week's regular target is shown separately from catch-up.",
            "In multi-week forecasts, existing arrears stay visible until actual contributions are recorded. Catch-up amounts already reserved in earlier projected weeks are not reserved again; each future week adds its regular target. Future unfunded targets do not become new arrears until that week occurs.",
            "Goals without a target date and with a positive weekly target are ongoing savings; a zero-amount undated goal remains an open-ended general savings goal.",
            "Only active scheduled income, bills, debts, and savings goals are included.",
            "Link a recorded transaction to the scheduled item it paid or received to prevent duplicate counting.",
            "Linked transactions match scheduled occurrences within seven days, or the bill's larger flexible window.",
            "Linked debt payments reduce that cycle's minimum by the amount paid; payments above the minimum are shown as extra debt reduction.",
            "Debt minimums are capped at the recorded outstanding balance. Payment matching is a planning assumption and may differ from lender payment-application rules.",
            "Transfers are listed for reference but do not change household cash totals.",
            "All monetary calculations use integer cents; this report never initiates payments or transfers.",
            "Starting cash is a confirmed household snapshot effective at the beginning of its financial week; snapshots are not automatically inferred from transactions.",
            (
                "WARNING: no confirmed starting-cash snapshot is available for this week. Set cash on hand in Household & income."
                if starting_cash_confirmed_at is None
                else f"WARNING: starting cash was last confirmed more than {CASH_SNAPSHOT_STALE_DAYS} days ago. Reconfirm it in Household & income."
            ) if starting_cash_is_stale else "Starting cash confirmation is current.",
        ],
    }


@router.api_route("/api/surplus-allocations", methods=["GET", "POST"])
def retired_surplus_allocations():
    """Reject the retired safe-surplus allocation workflow."""
    raise HTTPException(
        status_code=410,
        detail="Safe-surplus allocations were retired. Use the rolling pay-period forecast and record actual payments or savings contributions directly.",
    )


@router.post("/api/surplus-allocations/{allocation_id}/{action}")
def retired_surplus_allocation_action(allocation_id: int, action: str):
    """Reject legacy allocation actions while preserving old records for history."""
    raise HTTPException(
        status_code=410,
        detail="Safe-surplus allocations were retired. Record actual payments or savings contributions from their current management pages.",
    )


@router.get("/weekly")
def weekly_report_page():
    """Send legacy weekly-report bookmarks to the current pay-period forecast."""
    return RedirectResponse(url="/pay-periods", status_code=307)

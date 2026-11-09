"""Household-wide cash settings and dated, confirmed cash snapshots."""
import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from . import database
from .household_schemas import HouseholdSettingsOut, HouseholdSettingsUpdate

router = APIRouter()

CASH_SNAPSHOT_STALE_DAYS = 10


def _current_financial_week_start(db, today: date | None = None) -> date:
    row = db.execute(
        "SELECT financial_week_start FROM household_settings WHERE id = 1"
    ).fetchone()
    weekday = {
        "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
        "Friday": 4, "Saturday": 5, "Sunday": 6,
    }.get(row["financial_week_start"] if row else "Friday", 4)
    today = today or date.today()
    return today - timedelta(days=(today.weekday() - weekday) % 7)


def _read_settings(db) -> dict:
    row = db.execute(
        "SELECT starting_cash_cents, financial_week_start, paycheck_group_window_days, end_of_period_target_cents, updated_at "
        "FROM household_settings WHERE id = 1"
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="Household settings are not initialized")
    result = dict(row)
    current_week = _current_financial_week_start(db)
    snapshot = db.execute(
        """SELECT effective_week_start, balance_cents, confirmed_at
           FROM cash_balance_snapshots
           WHERE effective_week_start <= ?
           ORDER BY effective_week_start DESC, id DESC LIMIT 1""",
        (current_week.isoformat(),),
    ).fetchone()
    if snapshot:
        result["starting_cash_cents"] = snapshot["balance_cents"]
        result["starting_cash_effective_week"] = snapshot["effective_week_start"]
        result["starting_cash_confirmed_at"] = snapshot["confirmed_at"]
        try:
            confirmed_date = date.fromisoformat(snapshot["confirmed_at"][:10])
            result["starting_cash_is_stale"] = (date.today() - confirmed_date).days > CASH_SNAPSHOT_STALE_DAYS
        except (TypeError, ValueError):
            result["starting_cash_is_stale"] = True

        # Suggest a balance for the current week's opening only when the latest
        # confirmed snapshot is older than this week. Transactions on/after that
        # snapshot and before the current week are applied once; transactions
        # within the current week remain separate forecast activity.
        if date.fromisoformat(snapshot["effective_week_start"]) < current_week:
            activity = db.execute(
                """SELECT COALESCE(SUM(amount_cents), 0) AS delta_cents, COUNT(*) AS transaction_count
                   FROM transactions
                   WHERE transaction_date >= ? AND transaction_date < ?
                     AND transaction_type IN ('income', 'expense')""",
                (snapshot["effective_week_start"], current_week.isoformat()),
            ).fetchone()
            result["suggested_cash_balance_cents"] = snapshot["balance_cents"] + activity["delta_cents"]
            result["suggested_cash_effective_week"] = current_week
            result["suggested_cash_transaction_count"] = activity["transaction_count"]
            result["suggested_cash_based_on_effective_week"] = snapshot["effective_week_start"]
        else:
            result["suggested_cash_balance_cents"] = None
            result["suggested_cash_effective_week"] = None
            result["suggested_cash_transaction_count"] = 0
            result["suggested_cash_based_on_effective_week"] = None
    else:
        result["starting_cash_effective_week"] = None
        result["starting_cash_confirmed_at"] = None
        result["starting_cash_is_stale"] = True
        result["suggested_cash_balance_cents"] = None
        result["suggested_cash_effective_week"] = None
        result["suggested_cash_transaction_count"] = 0
        result["suggested_cash_based_on_effective_week"] = None
    return result


@router.get("/api/household/settings", response_model=HouseholdSettingsOut)
def get_household_settings():
    with database.connect() as db:
        return _read_settings(db)


@router.patch("/api/household/settings", response_model=HouseholdSettingsOut)
def update_household_settings(body: HouseholdSettingsUpdate):
    updates = body.model_dump(exclude_unset=True)
    effective_week = updates.pop("starting_cash_effective_week", None)
    if not updates:
        raise HTTPException(status_code=422, detail="no fields to update")
    if effective_week is not None and "starting_cash_cents" not in updates:
        raise HTTPException(
            status_code=422,
            detail="starting_cash_effective_week requires starting_cash_cents",
        )
    try:
        with database.connect() as db:
            if "starting_cash_cents" in updates:
                current_week = _current_financial_week_start(db)
                effective_week = effective_week or current_week
                if _current_financial_week_start(db, effective_week) != effective_week:
                    raise HTTPException(
                        status_code=422,
                        detail="Cash snapshots must be dated on the first day of the configured financial week",
                    )
                if effective_week > current_week:
                    raise HTTPException(
                        status_code=422,
                        detail="A cash snapshot can only confirm the current or a past financial week",
                    )
                db.execute(
                    """INSERT INTO cash_balance_snapshots
                       (effective_week_start, balance_cents, confirmed_at)
                       VALUES (?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(effective_week_start) DO UPDATE SET
                           balance_cents = excluded.balance_cents,
                           confirmed_at = CURRENT_TIMESTAMP""",
                    (effective_week.isoformat(), updates["starting_cash_cents"]),
                )
                latest = db.execute(
                    """SELECT balance_cents FROM cash_balance_snapshots
                       WHERE effective_week_start <= ?
                       ORDER BY effective_week_start DESC, id DESC LIMIT 1""",
                    (current_week.isoformat(),),
                ).fetchone()
                updates["starting_cash_cents"] = latest["balance_cents"] if latest else 0
            set_clause = ", ".join(f"{column} = ?" for column in updates)
            db.execute(
                f"UPDATE household_settings SET {set_clause}, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                (*updates.values(),),
            )
            return _read_settings(db)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=422, detail="Update violates a database constraint"
        ) from exc


@router.get("/pay-period-settings", response_class=HTMLResponse)
def pay_period_settings_page():
    """Edit the household paycheck grouping window."""
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pay Period Settings · CashFlow Engine</title></head><body><main>
<h1>Pay Period Settings</h1>
<p class="muted">Choose how close together household paychecks must be to belong to one pay group. The default is 2 calendar days.</p>
<section class="card">
<form id="pay-period-settings-form">
<label for="end-of-period-target">End-of-period cash target ($)</label>
<input id="end-of-period-target" name="end_of_period_target_cents" type="number" min="0" step="0.01" required>
<label for="paycheck-group-window">Group paychecks within (calendar days)</label>
<input id="paycheck-group-window" name="paycheck_group_window_days" type="number" min="0" max="14" step="1" required>
<p class="muted">Each paycheck is compared with the first paycheck in its group, not the previous paycheck. This prevents a chain of paydays from stretching a group. A group’s pay period starts on its last payday; the next period starts on the next group’s last payday.</p>
<button type="submit">Save setting</button>
</form>
<p id="pay-period-settings-status" class="muted" role="status">Loading setting…</p>
</section></main>
<script>
const field=document.getElementById("paycheck-group-window");
const targetField=document.getElementById("end-of-period-target");
const status=document.getElementById("pay-period-settings-status");
async function loadSetting(){const r=await fetch("/api/household/settings");const d=await r.json();if(!r.ok)throw Error(d.detail||"Could not load settings.");field.value=d.paycheck_group_window_days??2;targetField.value=((d.end_of_period_target_cents??0)/100).toFixed(2);status.textContent="Current grouping window: "+field.value+" day(s).";}
document.getElementById("pay-period-settings-form").addEventListener("submit",async e=>{e.preventDefault();const r=await fetch("/api/household/settings",{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({paycheck_group_window_days:Number(field.value),end_of_period_target_cents:Math.round(Number(targetField.value)*100)})});const d=await r.json();if(!r.ok){status.textContent=d.detail||"Could not save setting.";return;}field.value=d.paycheck_group_window_days;targetField.value=(d.end_of_period_target_cents/100).toFixed(2);status.textContent="Saved. Paychecks within "+d.paycheck_group_window_days+" calendar day(s) of the first payday are grouped together.";});
loadSetting().catch(e=>status.textContent=e.message);
</script></body></html>"""

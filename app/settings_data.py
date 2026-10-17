"""Backup download and guarded household-data reset endpoints."""
import sqlite3

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import database
from scripts.backup_database import create_backup

router = APIRouter()

CONFIRMATION_PHRASE = "DELETE ALL DATA"
FINANCIAL_TABLES = (
    "plan_items",
    "payday_plans",
    "transactions",
    "income_sources",
    "bills",
    "credit_cards",
    "savings_goals",
    "savings_accounts",
    "persons",
)


class ClearAllDataRequest(BaseModel):
    confirmation_phrase: str = Field(min_length=1, max_length=40)


@router.get("/api/settings-data/backup")
def download_database_backup():
    """Create a consistent server-side backup and return it as a download."""
    try:
        path = create_backup(database.database_path())
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Could not create a database backup") from exc
    return FileResponse(
        path=path,
        media_type="application/octet-stream",
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/settings-data/clear-all-data")
def clear_all_data(body: ClearAllDataRequest):
    """Back up first, then delete household financial records in one transaction."""
    if body.confirmation_phrase != CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=422,
            detail=f'Type "{CONFIRMATION_PHRASE}" exactly to confirm this reset',
        )

    # A failed backup must never be followed by destructive database changes.
    try:
        backup_path = create_backup(database.database_path())
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail="Reset cancelled because a verified database backup could not be created",
        ) from exc

    deleted_counts = {}
    try:
        with database.connect() as db:
            db.execute("BEGIN")
            for table in FINANCIAL_TABLES:
                deleted_counts[table] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in FINANCIAL_TABLES:
                db.execute(f"DELETE FROM {table}")
            db.execute(
                """UPDATE household_settings
                   SET buffer_target_cents = 0, starting_cash_cents = 0,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = 1"""
            )
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Reset failed; the database transaction was rolled back. Backup saved as {backup_path.name}",
        ) from exc

    return {
        "cleared": True,
        "backup_created": True,
        "backup_file": backup_path.name,
        "deleted_records": deleted_counts,
        "household_settings_reset": True,
        "categories_preserved": True,
    }


@router.get("/settings-data", response_class=HTMLResponse)
def settings_data_page():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Settings & Data · CashFlow Engine</title>
<style>
:root{font-family:system-ui,sans-serif;color:#172033;background:#f3f6fa}*{box-sizing:border-box}
body{margin:0;padding:24px 14px 48px}main{max-width:950px;margin:auto}a{color:#0e7490;font-weight:650;text-decoration:none}
h1{color:#155e75;margin:8px 0}h2{font-size:1.15rem;margin:0 0 10px}.muted{color:#5c687a;line-height:1.5}
.card{background:white;padding:22px;border-radius:16px;box-shadow:0 8px 28px #172b4d0d;margin-top:18px}
button{border:0;border-radius:8px;padding:11px 14px;background:#0e7490;color:white;font-weight:700;cursor:pointer}
button:disabled{opacity:.55;cursor:wait}.danger{background:#b91c1c}
label{display:block;font-size:.92rem;font-weight:700;margin:14px 0 6px}
input{width:100%;padding:11px;border:1px solid #cbd5e1;border-radius:8px;font:inherit}
.warning{background:#fff7ed;color:#9a3412;padding:13px;border-radius:9px;line-height:1.5}
.status{padding:12px;border-radius:9px;background:#e0f2fe;color:#075985;margin-top:16px}.error{background:#fee2e2;color:#991b1b}.status[hidden]{display:none}
</style></head><body><main><a href="/">← Dashboard</a><h1>Settings & Data</h1>
<p class="muted">Back up your household data or reset the financial records while keeping CashFlow Engine installed and ready for a fresh setup.</p>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<section class="card"><h2>Back up your database</h2>
<p class="muted">Create a consistent SQLite backup download. A separate server-side backup is also created automatically before any reset.</p>
<button id="backup" type="button">Create & download backup</button></section>
<section class="card"><h2>Clear all financial data</h2>
<div class="warning"><strong>This cannot be undone from the app.</strong> The reset will first create and verify a backup, then delete household members, income sources, bills, debts, savings accounts and goals, transactions, saved payday plans, and checklist items. The shared cash buffer and starting cash will be reset to $0. The database schema, application, and category definitions will remain.</div>
<form id="reset-form"><label for="confirmation">To confirm, type DELETE ALL DATA exactly</label>
<input id="confirmation" name="confirmation_phrase" required autocomplete="off" maxlength="40" placeholder="DELETE ALL DATA">
<p class="muted">The reset will not run if the backup cannot be created.</p>
<button class="danger" id="reset" type="submit">Back up and clear all data</button></form></section>
</main><script>
const box=document.getElementById("status");
function message(text,error=false){box.textContent=text;box.className="status"+(error?" error":"");box.hidden=false;}
async function jsonApi(url,options={}){const r=await fetch(url,{headers:{"Content-Type":"application/json"},...options});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"The request failed.");return d;}
document.getElementById("backup").addEventListener("click",async()=>{const button=document.getElementById("backup");button.disabled=true;try{const response=await fetch("/api/settings-data/backup");if(!response.ok){const data=await response.json().catch(()=>({}));throw Error(data.detail||"Could not create backup.");}const blob=await response.blob();const disposition=response.headers.get("content-disposition")||"";const match=disposition.match(/filename="?([^";]+)"?/i);const filename=match?match[1]:"cashflow-backup.db";const url=URL.createObjectURL(blob);const link=document.createElement("a");link.href=url;link.download=filename;document.body.appendChild(link);link.click();link.remove();URL.revokeObjectURL(url);message("Backup created and downloaded: "+filename);}catch(e){message(e.message,true);}finally{button.disabled=false;}});
document.getElementById("reset-form").addEventListener("submit",async(e)=>{e.preventDefault();if(document.getElementById("confirmation").value!=="DELETE ALL DATA"){message("The confirmation phrase must match exactly.",true);return;}if(!confirm("This will delete the household financial records. A verified backup is created first. Continue?"))return;const button=document.getElementById("reset");button.disabled=true;try{const result=await jsonApi("/api/settings-data/clear-all-data",{method:"POST",body:JSON.stringify({confirmation_phrase:document.getElementById("confirmation").value})});document.getElementById("confirmation").value="";const total=Object.values(result.deleted_records).reduce((sum,count)=>sum+count,0);message("All financial data was cleared. "+total+" records removed. Safety backup: "+result.backup_file+". Category definitions were kept.");}catch(err){message(err.message,true);}finally{button.disabled=false;}});
</script></body></html>"""

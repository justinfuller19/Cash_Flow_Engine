"""Manual transaction ledger API and browser page."""
import sqlite3
from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from . import database
from .transaction_schemas import TransactionCreate, TransactionOut, TransactionUpdate

router = APIRouter()


def _clean(values: dict) -> dict:
    for field in ("category", "description"):
        if field in values and values[field] is not None:
            values[field] = values[field].strip()
            if not values[field]:
                raise HTTPException(status_code=422, detail=f"{field} cannot be blank")
    if "category" in values and values["category"] is not None:
        values["category"] = values["category"].lower()
    if "transaction_date" in values and values["transaction_date"] is not None:
        values["transaction_date"] = values["transaction_date"].isoformat()
    return values


def _validate_category(db, category: str, transaction_type: str) -> None:
    exists = db.execute(
        "SELECT id FROM categories WHERE lower(name) = lower(?) AND transaction_type = ?",
        (category, transaction_type),
    ).fetchone()
    if exists is None:
        raise HTTPException(status_code=422, detail="Choose a category that belongs to the selected transaction type")


def _validate_link(db, transaction_type: str, link_type: str | None, link_id: int | None) -> None:
    if (link_type is None) != (link_id is None):
        raise HTTPException(status_code=422, detail="Select both a scheduled item type and item")
    if link_type is None:
        return
    if (link_type == "income" and transaction_type != "income") or (
        link_type in {"bill", "debt"} and transaction_type != "expense"
    ):
        raise HTTPException(status_code=422, detail="Scheduled item type must match the transaction type")
    table = {"income": "income_sources", "bill": "bills", "debt": "credit_cards"}[link_type]
    if db.execute(f"SELECT id FROM {table} WHERE id = ?", (link_id,)).fetchone() is None:
        raise HTTPException(status_code=422, detail="Linked scheduled item was not found")


@router.get("/api/transactions", response_model=list[TransactionOut])
def list_transactions(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
):
    if start_date and end_date and end_date < start_date:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    clauses = []
    params = []
    if start_date:
        clauses.append("transaction_date >= ?")
        params.append(start_date.isoformat())
    if end_date:
        clauses.append("transaction_date <= ?")
        params.append(end_date.isoformat())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with database.connect() as db:
        rows = db.execute(
            """SELECT id, transaction_date, category, transaction_type, linked_obligation_type, linked_obligation_id, description, amount_cents,
                      account_name, notes, created_at
               FROM transactions""" + where + " ORDER BY transaction_date DESC, id DESC LIMIT 500",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("/api/transactions", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def create_transaction(body: TransactionCreate):
    values = _clean(body.model_dump())
    transaction_type = values["transaction_type"] or (
        "income" if values["category"] in {
            "income", "paycheck", "paychecks", "side income", "reimbursements", "other income"
        } else "expense"
    )
    try:
        with database.connect() as db:
            _validate_category(db, values["category"], transaction_type)
            _validate_link(db, transaction_type, values["linked_obligation_type"], values["linked_obligation_id"])
            cursor = db.execute(
                """INSERT INTO transactions
                   (transaction_date, category, transaction_type, linked_obligation_type, linked_obligation_id, description, amount_cents, account_name, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (values["transaction_date"], values["category"], transaction_type, values["linked_obligation_type"],
                 values["linked_obligation_id"], values["description"], values["amount_cents"], values["account_name"], values["notes"]),
            )
            row = db.execute(
                """SELECT id, transaction_date, category, transaction_type, linked_obligation_type, linked_obligation_id, description, amount_cents,
                          account_name, notes, created_at
                   FROM transactions WHERE id = ?""", (cursor.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Transaction violates a database constraint") from exc
    return dict(row)


@router.patch("/api/transactions/{transaction_id}", response_model=TransactionOut)
def update_transaction(transaction_id: int, body: TransactionUpdate):
    updates = _clean(body.model_dump(exclude_unset=True))
    if not updates:
        raise HTTPException(status_code=422, detail="no fields to update")
    allowed = {"transaction_date", "category", "transaction_type", "linked_obligation_type", "linked_obligation_id", "description", "amount_cents", "account_name", "notes"}
    if not set(updates) <= allowed:
        raise HTTPException(status_code=422, detail="unsupported field")
    try:
        with database.connect() as db:
            current = db.execute(
                "SELECT id, category, transaction_type, linked_obligation_type, linked_obligation_id "
                "FROM transactions WHERE id = ?",
                (transaction_id,),
            ).fetchone()
            if current is None:
                raise HTTPException(status_code=404, detail="Transaction not found")
            category = updates.get("category", current["category"])
            transaction_type = updates.get("transaction_type", current["transaction_type"])
            link_type = updates.get("linked_obligation_type", current["linked_obligation_type"])
            link_id = updates.get("linked_obligation_id", current["linked_obligation_id"])
            _validate_category(db, category, transaction_type)
            _validate_link(db, transaction_type, link_type, link_id)
            updates["category"] = category
            updates["transaction_type"] = transaction_type
            updates["linked_obligation_type"] = link_type
            updates["linked_obligation_id"] = link_id
            assignments = ", ".join(f"{key} = ?" for key in updates)
            db.execute(f"UPDATE transactions SET {assignments} WHERE id = ?", (*updates.values(), transaction_id))
            row = db.execute(
                """SELECT id, transaction_date, category, transaction_type, linked_obligation_type, linked_obligation_id, description, amount_cents,
                          account_name, notes, created_at
                   FROM transactions WHERE id = ?""", (transaction_id,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail="Transaction update violates a database constraint") from exc
    return dict(row)


@router.delete("/api/transactions/{transaction_id}")
def delete_transaction(transaction_id: int):
    with database.connect() as db:
        cursor = db.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Transaction not found")
    return {"deleted": True, "id": transaction_id}


@router.get("/transactions", response_class=HTMLResponse)
def transactions_page():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Transactions · CashFlow Engine</title>
<style>
:root{font-family:system-ui,sans-serif;color:#172033;background:#f3f6fa}*{box-sizing:border-box}
body{margin:0;padding:24px 14px 48px}main{max-width:1100px;margin:auto}a{color:#0e7490;font-weight:650;text-decoration:none}
h1{color:#155e75;margin:8px 0}.muted{color:#5c687a;line-height:1.5}
.card{background:white;padding:22px;border-radius:16px;box-shadow:0 8px 28px #172b4d0d;margin-top:18px}
form{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr));gap:12px;align-items:end}
label{display:block;font-size:.9rem;font-weight:650;margin-bottom:5px}input,select{width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font:inherit;background:white}
button{border:0;border-radius:8px;padding:10px 12px;background:#0e7490;color:white;font-weight:700;cursor:pointer}.edit{background:#475569}
.status{padding:12px;border-radius:9px;background:#e0f2fe;color:#075985}.error{background:#fee2e2;color:#991b1b}.status[hidden]{display:none}
.list{padding:0;list-style:none}.list li{padding:13px 0;border-top:1px solid #e2e8f0;overflow-wrap:anywhere}
.title{font-weight:700}.detail{color:#5c687a;font-size:.9rem;margin-top:4px}.positive{color:#166534}.negative{color:#b91c1c}
</style></head><body><main><a href="/">← Dashboard</a> · <a href="/categories">Manage Categories</a><h1>Transactions</h1>
<p class="muted">Record actual account activity for reference. Enter income as a positive amount and expenses as a negative amount in the ledger. These entries do not automatically change bank balances or rewrite saved payday plans.</p>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<section class="card"><h2>Add a transaction</h2><form id="transaction-form">
<div><label for="tx-date">Date</label><input id="tx-date" name="transaction_date" type="date" required></div>
<div><label for="tx-type">Type</label><select id="tx-type" name="transaction_type"><option value="expense">Expense</option><option value="income">Income</option><option value="transfer">Transfer</option></select></div>
<div><label for="tx-amount">Amount ($)</label><input id="tx-amount" name="amount" type="number" min="0.01" step="0.01" required></div>
<div><label for="tx-category">Category</label><select id="tx-category" name="category" required></select></div>
<div><label for="tx-linked">Scheduled item (optional)</label><select id="tx-linked" name="linked_obligation"><option value="">Not linked</option></select></div>
<div><label for="tx-description">Description</label><input id="tx-description" name="description" maxlength="200" required placeholder="Grocery store, paycheck"></div>
<div><label for="tx-account">Account (optional)</label><input id="tx-account" name="account_name" maxlength="120" placeholder="Checking, Visa"></div>
<div><label for="tx-notes">Notes (optional)</label><input id="tx-notes" name="notes" maxlength="1000"></div>
<div><button type="submit">Save transaction</button></div>
</form></section>
<section class="card"><h2>Recent transactions</h2><ul id="transactions" class="list"><li>Loading transactions…</li></ul></section>
</main><script>
const box=document.getElementById("status");
function message(text,error=false){box.textContent=text;box.classList.toggle("error",error);box.hidden=false;}
function dollars(cents){const sign=cents<0?"−":"";return sign+new Intl.NumberFormat(undefined,{style:"currency",currency:"USD"}).format(Math.abs(cents)/100);}
function cents(value){const n=Number(value);if(!Number.isFinite(n)||n<=0)throw Error("Enter an amount greater than zero.");return Math.round((n+Number.EPSILON)*100);}
async function api(url,options={}){const r=await fetch(url,{headers:{"Content-Type":"application/json"},...options});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"Please check your entries.");return d;}
function el(tag,cls,txt){const n=document.createElement(tag);if(cls)n.className=cls;n.textContent=txt;return n;}
async function refresh(){const rows=await api("/api/transactions");const list=document.getElementById("transactions");list.replaceChildren();if(!rows.length){list.appendChild(el("li","muted","No transactions recorded yet."));return;}
for(const row of rows){const li=el("li","");li.appendChild(el("div","title",row.description+" · "+dollars(row.amount_cents)));li.appendChild(el("div","detail",row.transaction_date+" · "+row.transaction_type+" · "+row.category+(row.linked_obligation_type?" · linked "+row.linked_obligation_type+" #"+row.linked_obligation_id:"")+(row.account_name?" · "+row.account_name:"")+(row.notes?" · "+row.notes:"")));
const edit=el("button","edit","Edit");edit.type="button";edit.addEventListener("click",async()=>{const description=prompt("Description:",row.description);if(description===null)return;const transactionType=prompt("Type (income, expense, transfer):",row.transaction_type);if(transactionType===null)return;const category=prompt("Category:",row.category);if(category===null)return;const link=prompt("Scheduled item link (blank, or income:ID, bill:ID, debt:ID):",row.linked_obligation_type?row.linked_obligation_type+":"+row.linked_obligation_id:"");if(link===null)return;const amount=prompt("Amount in dollars (use a positive amount; type controls classification):",Math.abs(row.amount_cents/100).toFixed(2));if(amount===null)return;const txDate=prompt("Date YYYY-MM-DD:",row.transaction_date);if(txDate===null)return;const account=prompt("Account (blank to clear):",row.account_name||"");if(account===null)return;const notes=prompt("Notes (blank to clear):",row.notes||"");if(notes===null)return;try{const n=Number(amount);if(!Number.isFinite(n))throw Error("Enter a valid amount.");if(!["income","expense","transfer"].includes(transactionType))throw Error("Type must be income, expense, or transfer.");const linkParts=link.trim()?link.trim().split(":"):[];if(linkParts.length&&(linkParts.length!==2||!["income","bill","debt"].includes(linkParts[0])||!Number.isInteger(Number(linkParts[1]))||Number(linkParts[1])<1))throw Error("Link must be blank or use income:ID, bill:ID, or debt:ID.");await api("/api/transactions/"+row.id,{method:"PATCH",body:JSON.stringify({description,category,transaction_type:transactionType,linked_obligation_type:linkParts[0]||null,linked_obligation_id:linkParts.length?Number(linkParts[1]):null,amount_cents:Math.round(n*100)*(transactionType==="expense"?-1:1),transaction_date:txDate,account_name:account||null,notes:notes||null})});await refresh();message("Transaction updated.");}catch(e){message(e.message,true);}});li.appendChild(edit);
const remove=el("button","edit","Delete");remove.type="button";remove.addEventListener("click",async()=>{if(!confirm("Permanently delete transaction "+row.description+"?"))return;remove.disabled=true;try{await api("/api/transactions/"+row.id,{method:"DELETE"});await refresh();message("Transaction deleted.");}catch(e){message(e.message,true);remove.disabled=false;}});li.appendChild(remove);list.appendChild(li);}}
async function loadCategories(){const type=document.getElementById("tx-type").value;const categories=await api("/api/categories?transaction_type="+encodeURIComponent(type));const select=document.getElementById("tx-category");select.replaceChildren();for(const category of categories){const option=document.createElement("option");option.value=category.name;option.textContent=category.name.split(" ").map(w=>w.charAt(0).toUpperCase()+w.slice(1)).join(" ");select.appendChild(option);}if(!categories.length)select.appendChild(new Option("Add a category first",""));await loadScheduledItems(type);}
async function loadScheduledItems(type){const select=document.getElementById("tx-linked");select.replaceChildren();select.appendChild(new Option("Not linked",""));if(type==="income"){const rows=await api("/api/income-sources");for(const row of rows.filter(x=>x.active)){select.appendChild(new Option("Income: "+row.name,"income:"+row.id));}}else if(type==="expense"){const [bills,debts]=await Promise.all([api("/api/bills"),api("/api/debts")]);for(const row of bills.filter(x=>x.active))select.appendChild(new Option("Bill: "+row.name,"bill:"+row.id));for(const row of debts.filter(x=>x.active))select.appendChild(new Option("Debt: "+row.name,"debt:"+row.id));}}
document.getElementById("tx-type").addEventListener("change",()=>loadCategories().catch(e=>message(e.message,true)));
document.getElementById("transaction-form").addEventListener("submit",async(e)=>{e.preventDefault();const f=e.currentTarget;const b=f.querySelector("button");b.disabled=true;try{let amount=cents(f.elements.amount.value);const transactionType=f.elements.transaction_type.value;if(transactionType==="expense")amount=-amount;const linkValue=f.elements.linked_obligation.value;const linkParts=linkValue?linkValue.split(":"):[];await api("/api/transactions",{method:"POST",body:JSON.stringify({transaction_date:f.elements.transaction_date.value,transaction_type:transactionType,category:f.elements.category.value,linked_obligation_type:linkParts[0]||null,linked_obligation_id:linkParts.length?Number(linkParts[1]):null,description:f.elements.description.value.trim(),amount_cents:amount,account_name:f.elements.account_name.value.trim()||null,notes:f.elements.notes.value.trim()||null})});f.reset();f.elements.transaction_date.value=new Date().toISOString().slice(0,10);await loadCategories();await refresh();message("Transaction saved.");}catch(err){message(err.message,true);}finally{b.disabled=false;}});
document.getElementById("tx-date").value=new Date().toISOString().slice(0,10);
refresh().catch(e=>message("Could not load transactions: "+e.message,true));
</script></body></html>"""

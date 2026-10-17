"""User-managed transaction categories with safe history-preserving edits."""
import sqlite3

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import database

router = APIRouter()


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    transaction_type: str = Field(pattern="^(income|expense|transfer)$")


class CategoryRename(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class CategoryDelete(BaseModel):
    replacement_category_id: int


def _name(value: str) -> str:
    value = value.strip().lower()
    if not value:
        raise HTTPException(status_code=422, detail="name cannot be blank")
    return value


def _category(db, category_id: int):
    row = db.execute(
        "SELECT id, name, transaction_type FROM categories WHERE id = ?", (category_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return row


@router.get("/api/categories")
def list_categories(transaction_type: str | None = Query(default=None)):
    if transaction_type is not None and transaction_type not in {"income", "expense", "transfer"}:
        raise HTTPException(status_code=422, detail="Invalid transaction type")
    with database.connect() as db:
        if transaction_type:
            rows = db.execute(
                "SELECT id, name, transaction_type FROM categories "
                "WHERE transaction_type = ? ORDER BY name COLLATE NOCASE, id",
                (transaction_type,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, name, transaction_type FROM categories "
                "ORDER BY transaction_type, name COLLATE NOCASE, id"
            ).fetchall()
    return [dict(row) for row in rows]


@router.post("/api/categories", status_code=status.HTTP_201_CREATED)
def create_category(body: CategoryCreate):
    name = _name(body.name)
    try:
        with database.connect() as db:
            cursor = db.execute(
                "INSERT INTO categories (name, transaction_type) VALUES (?, ?)",
                (name, body.transaction_type),
            )
            return dict(_category(db, cursor.lastrowid))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail="A category with that name already exists for this type"
        ) from exc


@router.patch("/api/categories/{category_id}")
def rename_category(category_id: int, body: CategoryRename):
    name = _name(body.name)
    try:
        with database.connect() as db:
            current = _category(db, category_id)
            if current["name"] == "other" and name != "other":
                raise HTTPException(status_code=422, detail="The Other category must remain available")
            db.execute("UPDATE categories SET name = ? WHERE id = ?", (name, category_id))
            # The ledger keeps a readable snapshot string for simple exports; keep it
            # synchronized so renames immediately update existing transaction history.
            db.execute(
                "UPDATE transactions SET category = ? WHERE transaction_type = ? AND lower(category) = lower(?)",
                (name, current["transaction_type"], current["name"]),
            )
            return dict(_category(db, category_id))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail="A category with that name already exists for this type"
        ) from exc


@router.delete("/api/categories/{category_id}")
def delete_category(category_id: int, body: CategoryDelete):
    with database.connect() as db:
        current = _category(db, category_id)
        replacement = _category(db, body.replacement_category_id)
        if current["id"] == replacement["id"]:
            raise HTTPException(status_code=422, detail="Choose a different replacement category")
        if current["name"] == "other":
            raise HTTPException(status_code=422, detail="The Other category cannot be deleted")
        if current["transaction_type"] != replacement["transaction_type"]:
            raise HTTPException(
                status_code=422, detail="Replacement category must have the same transaction type"
            )
        db.execute(
            "UPDATE transactions SET category = ? WHERE transaction_type = ? AND lower(category) = lower(?)",
            (replacement["name"], current["transaction_type"], current["name"]),
        )
        db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    return {"deleted": True, "id": category_id, "reassigned_to": replacement["name"]}


@router.get("/categories", response_class=HTMLResponse)
def categories_page():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Manage Categories · CashFlow Engine</title>
<style>
:root{font-family:system-ui,sans-serif;color:#172033;background:#f3f6fa}*{box-sizing:border-box}
body{margin:0;padding:24px 14px 48px}main{max-width:950px;margin:auto}a{color:#0e7490;font-weight:650;text-decoration:none}
h1{color:#155e75;margin:8px 0}.muted{color:#5c687a;line-height:1.5}
.card{background:white;padding:22px;border-radius:16px;box-shadow:0 8px 28px #172b4d0d;margin-top:18px}
form{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,180px),1fr));gap:12px;align-items:end}
label{display:block;font-size:.9rem;font-weight:650;margin-bottom:5px}
input,select{width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font:inherit;background:white}
button{border:0;border-radius:8px;padding:10px 12px;background:#0e7490;color:white;font-weight:700;cursor:pointer}
button.secondary{background:#475569}button.danger{background:#b91c1c}
.status{padding:12px;border-radius:9px;background:#e0f2fe;color:#075985}.error{background:#fee2e2;color:#991b1b}.status[hidden]{display:none}
.item{padding:14px 0;border-top:1px solid #e2e8f0}.item:first-child{border-top:0}
.detail{font-size:.9rem;color:#5c687a;margin:4px 0 10px}.actions{display:flex;flex-wrap:wrap;gap:8px}
</style></head><body><main><a href="/">← Dashboard</a> · <a href="/transactions">Transactions</a><h1>Manage Categories</h1>
<p class="muted">Create categories for income, expenses, and transfers. Renaming a category updates its existing transaction labels. Deleting a category reassigns its transactions to another category of the same type; transactions themselves are never deleted.</p>
<div id="status" class="status" role="status" aria-live="polite" hidden></div>
<section class="card"><h2>Add category</h2><form id="create-form">
<div><label for="category-name">Name</label><input id="category-name" name="name" maxlength="40" required placeholder="e.g. Pet care"></div>
<div><label for="category-type">Type</label><select id="category-type" name="transaction_type"><option value="expense">Expense</option><option value="income">Income</option><option value="transfer">Transfer</option></select></div>
<div><button type="submit">Add category</button></div></form></section>
<section class="card"><h2>Existing categories</h2><div id="categories">Loading…</div></section></main>
<script>
const box=document.getElementById("status");
function message(text,error=false){box.textContent=text;box.classList.toggle("error",error);box.hidden=false;}
async function api(url,options={}){const r=await fetch(url,{headers:{"Content-Type":"application/json"},...options});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"Please check your entries.");return d;}
function el(tag,cls,txt){const n=document.createElement(tag);if(cls)n.className=cls;if(txt!==undefined)n.textContent=txt;return n;}
function title(value){return value.replace(/\b\w/g,c=>c.toUpperCase());}
async function refresh(){const rows=await api("/api/categories");const root=document.getElementById("categories");root.replaceChildren();
if(!rows.length){root.appendChild(el("p","muted","No categories yet."));return;}
for(const category of rows){const item=el("div","item");item.appendChild(el("strong","",title(category.name)));item.appendChild(el("div","detail",title(category.transaction_type)));
const actions=el("div","actions");const rename=el("button secondary","","Rename");rename.type="button";rename.addEventListener("click",async()=>{const name=prompt("New category name:",title(category.name));if(name===null)return;try{await api("/api/categories/"+category.id,{method:"PATCH",body:JSON.stringify({name})});await refresh();message("Category renamed; existing transactions were updated.");}catch(e){message(e.message,true);}});
actions.appendChild(rename);
const remove=el("button danger","","Delete");remove.type="button";remove.disabled=category.name==="other";
remove.title=remove.disabled?"The Other category is required as a safe fallback":"Delete and reassign existing transactions";
remove.addEventListener("click",async()=>{try{const candidates=rows.filter(x=>x.transaction_type===category.transaction_type&&x.id!==category.id);if(!candidates.length)throw Error("Add another category of this type before deleting this one.");
const options=candidates.map((x,i)=>(i+1)+". "+title(x.name)).join("\n");const selected=prompt("Transactions in this category will be reassigned. Choose a replacement by number:\n"+options,"1");if(selected===null)return;const index=Number(selected)-1;if(!Number.isInteger(index)||index<0||index>=candidates.length)throw Error("Choose a valid replacement number.");
if(!confirm("Delete "+title(category.name)+" and move its transactions to "+title(candidates[index].name)+"?"))return;
await api("/api/categories/"+category.id,{method:"DELETE",body:JSON.stringify({replacement_category_id:candidates[index].id})});await refresh();message("Category deleted; transaction history was preserved.");}catch(e){message(e.message,true);}});
actions.appendChild(remove);item.appendChild(actions);root.appendChild(item);}}
document.getElementById("create-form").addEventListener("submit",async(e)=>{e.preventDefault();const form=e.currentTarget;try{await api("/api/categories",{method:"POST",body:JSON.stringify({name:form.elements.name.value,transaction_type:form.elements.transaction_type.value})});form.reset();await refresh();message("Category added.");}catch(err){message(err.message,true);}});
refresh().catch(e=>message("Could not load categories: "+e.message,true));
</script></body></html>"""

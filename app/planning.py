"""Persisted payday planning from the saved household setup."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from . import database
from .planning_schemas import SavedPlanOut

router = APIRouter()


@router.post("/api/plans/generate")
def retired_plan_generation():
    """Reject new plans from the retired single-payday allocation engine."""
    raise HTTPException(
        status_code=410,
        detail="Single-payday allocation plans were retired. Use the rolling three-month pay-period forecast.",
    )


def _plan_output(db, row) -> dict:
    plan = dict(row)
    items = db.execute(
        """SELECT id, category, name, requested_cents, funded_cents, unfunded_cents,
                  due_date, note, status, actual_amount_cents, actual_date, is_completed
           FROM plan_items WHERE plan_id = ? ORDER BY id""",
        (plan["id"],),
    ).fetchall()
    plan["items"] = [dict(item) | {"is_completed": bool(item["is_completed"])} for item in items]
    plan["is_fully_funded"] = plan["unfunded_cents"] == 0
    return plan


@router.get("/api/plans", response_model=list[SavedPlanOut])
def list_saved_plans():
    with database.connect() as db:
        rows = db.execute("SELECT * FROM payday_plans ORDER BY plan_date DESC, id DESC").fetchall()
        return [_plan_output(db, row) for row in rows]


@router.get("/api/plans/{plan_id}", response_model=SavedPlanOut)
def get_saved_plan(plan_id: int):
    with database.connect() as db:
        row = db.execute("SELECT * FROM payday_plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Payday plan not found")
        return _plan_output(db, row)


@router.delete("/api/plans/{plan_id}")
def delete_saved_plan(plan_id: int):
    with database.connect() as db:
        cursor = db.execute("DELETE FROM payday_plans WHERE id = ?", (plan_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Saved plan not found")
    return {"deleted": True, "id": plan_id}


@router.get("/planner")
def planner_page():
    return RedirectResponse(url="/checklist", status_code=307)


"""Household-wide cash settings: one shared buffer target and starting balance."""
import sqlite3

from fastapi import APIRouter, HTTPException

from . import database
from .household_schemas import HouseholdSettingsOut, HouseholdSettingsUpdate

router = APIRouter()


def _read_settings(db) -> dict:
    row = db.execute(
        "SELECT buffer_target_cents, starting_cash_cents, updated_at "
        "FROM household_settings WHERE id = 1"
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="Household settings are not initialized")
    return dict(row)


@router.get("/api/household/settings", response_model=HouseholdSettingsOut)
def get_household_settings():
    with database.connect() as db:
        return _read_settings(db)


@router.patch("/api/household/settings", response_model=HouseholdSettingsOut)
def update_household_settings(body: HouseholdSettingsUpdate):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="no fields to update")
    try:
        with database.connect() as db:
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

"""Household-wide cash settings and dated, confirmed cash snapshots."""
import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException

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
        "SELECT buffer_target_cents, starting_cash_cents, financial_week_start, updated_at "
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
    else:
        result["starting_cash_effective_week"] = None
        result["starting_cash_confirmed_at"] = None
        result["starting_cash_is_stale"] = True
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

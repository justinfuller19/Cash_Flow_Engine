"""Tests for recurrence forecasting and retired payday-plan compatibility."""
from datetime import date

from fastapi.testclient import TestClient

from app import database
from app.forecast import occurrences_between
from app.main import app


def test_monthly_forecast_preserves_end_of_month_day():
    assert occurrences_between(
        date(2026, 1, 31), "monthly", date(2026, 2, 1), date(2026, 4, 30)
    ) == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


def test_weekly_biweekly_and_semimonthly_forecasts():
    assert occurrences_between(
        date(2026, 11, 6), "weekly", date(2026, 11, 1), date(2026, 11, 30)
    ) == [date(2026, 11, 6), date(2026, 11, 13), date(2026, 11, 20), date(2026, 11, 27)]
    assert occurrences_between(
        date(2026, 11, 6), "biweekly", date(2026, 11, 1), date(2026, 11, 30)
    ) == [date(2026, 11, 6), date(2026, 11, 20)]
    assert occurrences_between(
        date(2026, 11, 10), "semimonthly", date(2026, 11, 1), date(2026, 12, 15)
    ) == [date(2026, 11, 10), date(2026, 11, 15), date(2026, 12, 1), date(2026, 12, 15)]


def test_irregular_income_is_not_extrapolated():
    assert occurrences_between(
        date(2026, 11, 6), "irregular", date(2026, 11, 1), date(2026, 11, 30)
    ) == [date(2026, 11, 6)]
    assert occurrences_between(
        date(2026, 11, 6), "irregular", date(2026, 12, 1), date(2026, 12, 31)
    ) == []


def test_legacy_plan_generation_endpoints_return_gone(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "retired-plans.db"))
    database.initialize_database()
    client = TestClient(app)

    assert client.post("/api/plan/preview", json={}).status_code == 410
    assert client.post("/api/plans/generate", json={}).status_code == 410

    # Existing saved plans remain readable for historical reference.
    assert client.get("/api/plans").status_code == 200


def test_schema_v4_keeps_historical_plan_records(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "schema-v4.db"))
    database.initialize_database()
    with database.connect() as db:
        goal_columns = {row["name"] for row in db.execute("PRAGMA table_info(savings_goals)")}
        plan_columns = {row["name"] for row in db.execute("PRAGMA table_info(payday_plans)")}
        version = db.execute("PRAGMA user_version").fetchone()[0]
    assert "contribution_per_period_cents" in goal_columns
    assert {"income_cents", "total_available_cents", "allocated_cents",
            "unallocated_cents", "unfunded_cents", "target_card"} <= plan_columns
    assert version == database.SCHEMA_VERSION


def test_old_planner_url_redirects_to_current_checklist():
    client = TestClient(app)
    response = client.get("/planner", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/checklist"
    checklist = client.get("/checklist")
    assert checklist.status_code == 200
    assert "generated automatically" in checklist.text

"""Tests for recurrence forecasting and persisted payday planning."""
from datetime import date

from fastapi.testclient import TestClient

from app import database
from app.forecast import occurrences_between
from app.engine import (
    Bill, BufferReservation, CreditCard, PaydayRequest, SavingsGoal,
    generate_payday_plan,
)
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


def test_generate_plan_uses_saved_household_records_and_saves_history(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "planner.db"))
    database.initialize_database()
    client = TestClient(app)

    person = client.post("/api/people", json={"name": "Member"}).json()
    client.patch("/api/household/settings", json={"buffer_target_cents": 10000})
    client.post(
        "/api/income-sources",
        json={
            "person_id": person["id"],
            "name": "Paycheck",
            "net_amount_cents": 100000,
            "frequency": "weekly",
            "next_payday": "2026-11-21",
        },
    )
    client.post(
        "/api/bills",
        json={
            "name": "Electric",
            "amount_cents": 25000,
            "next_due_date": "2026-11-23",
            "frequency": "monthly",
            "category": "utilities",
        },
    )
    client.post(
        "/api/debts",
        json={
            "name": "Card",
            "balance_cents": 80000,
            "minimum_payment_cents": 5000,
            "apr_percent": 24.9,
            "next_due_date": "2026-11-22",
        },
    )
    client.post(
        "/api/savings-goals",
        json={
            "name": "Emergency fund",
            "target_amount_cents": 100000,
            "current_balance_cents": 0,
            "contribution_per_period_cents": 10000,
            "priority": 1,
        },
    )

    response = client.post(
        "/api/plans/generate",
        json={
            "plan_date": "2026-11-20",
            "period_start": "2026-11-20",
            "period_end": "2026-11-26",
            "starting_cash_cents": 50000,
            "debt_strategy": "avalanche",
        },
    )
    assert response.status_code == 201, response.text
    plan = response.json()
    assert plan["income_cents"] == 100000
    assert plan["total_available_cents"] == 150000
    assert plan["unallocated_cents"] == 25000
    assert plan["unfunded_cents"] == 0
    assert plan["is_fully_funded"] is True
    assert [item["category"] for item in plan["items"]] == [
        "bill", "debt_minimum", "buffer", "savings", "debt_extra"
    ]
    assert plan["items"][3]["requested_cents"] == 10000

    history = client.get("/api/plans")
    assert history.status_code == 200
    assert history.json()[0]["id"] == plan["id"]
    detail = client.get(f"/api/plans/{plan['id']}")
    assert detail.status_code == 200
    assert len(detail.json()["items"]) == 5
    assert client.get("/api/plans/9999").status_code == 404


def test_plan_validation_and_planner_page(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "planner-validation.db"))
    database.initialize_database()
    client = TestClient(app)
    response = client.post(
        "/api/plans/generate",
        json={
            "plan_date": "2026-11-20",
            "period_start": "2026-11-27",
            "period_end": "2026-11-20",
            "starting_cash_cents": 0,
        },
    )
    assert response.status_code == 422
    page = client.get("/planner")
    assert page.status_code == 200
    assert "Payday Planner" in page.text
    assert "/api/plans/generate" in page.text


def test_schema_v4_adds_contributions_and_plan_totals(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "schema-v4.db"))
    database.initialize_database()
    with database.connect() as db:
        goal_columns = {row["name"] for row in db.execute("PRAGMA table_info(savings_goals)")}
        plan_columns = {row["name"] for row in db.execute("PRAGMA table_info(payday_plans)")}
        version = db.execute("PRAGMA user_version").fetchone()[0]
    assert "contribution_per_period_cents" in goal_columns
    assert {"income_cents", "total_available_cents", "allocated_cents",
            "unallocated_cents", "unfunded_cents", "target_card"} <= plan_columns
    assert version == database.SCHEMA_VERSION == 7


def test_required_obligations_are_funded_before_underfunded_household_buffer():
    """A low buffer must not starve bills or debt minimums."""
    plan = generate_payday_plan(
        PaydayRequest(
            plan_date=date(2026, 11, 20),
            period_start=date(2026, 11, 20),
            period_end=date(2026, 11, 26),
            starting_cash_cents=50000,
            buffers=[BufferReservation(name="Household cash buffer", amount_cents=20000)],
            bills=[Bill(name="Rent", amount_cents=45000, due_date=date(2026, 11, 22))],
            credit_cards=[
                CreditCard(
                    name="Card",
                    balance_cents=30000,
                    minimum_payment_cents=10000,
                    due_date=date(2026, 11, 23),
                    apr_percent=24.9,
                )
            ],
            savings_goals=[
                SavingsGoal(
                    name="Emergency fund",
                    target_amount_cents=100000,
                    current_balance_cents=0,
                    remaining_periods=1,
                    contribution_per_period_cents=5000,
                )
            ],
        )
    )

    assert [item.category for item in plan.items] == [
        "bill", "debt_minimum", "buffer", "savings"
    ]
    bill, minimum, buffer, savings = plan.items
    assert (bill.funded_cents, bill.unfunded_cents) == (45000, 0)
    assert (minimum.funded_cents, minimum.unfunded_cents) == (5000, 5000)
    assert (buffer.funded_cents, buffer.unfunded_cents) == (0, 20000)
    assert (savings.funded_cents, savings.unfunded_cents) == (0, 5000)
    assert plan.allocated_cents == 50000
    assert plan.unallocated_cents == 0
    assert plan.unfunded_cents == 30000
    assert plan.total_available_cents == plan.allocated_cents + plan.unallocated_cents

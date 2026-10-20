"""Integration tests for persisted savings-goal management."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import database
from app.main import app


def client_for(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "savings.db"))
    database.initialize_database()
    return TestClient(app)


def test_create_list_edit_and_deactivate_savings_goal(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    response = client.post(
        "/api/savings-goals",
        json={
            "name": "Emergency fund",
            "target_amount_cents": 1000000,
            "current_balance_cents": 125000,
            "target_date": "2027-12-31",
            "priority": 1,
        },
    )
    assert response.status_code == 201, response.text
    goal = response.json()
    assert goal["target_amount_cents"] == 1000000
    assert goal["current_balance_cents"] == 125000
    assert goal["target_date"] == "2027-12-31"
    assert goal["active"] is True
    assert client.get("/api/savings-goals").json()[0]["id"] == goal["id"]

    response = client.patch(
        f"/api/savings-goals/{goal['id']}",
        json={"target_amount_cents": 1500000, "current_balance_cents": 200000, "priority": 2},
    )
    assert response.status_code == 200
    assert response.json()["target_amount_cents"] == 1500000
    assert response.json()["current_balance_cents"] == 200000
    assert response.json()["priority"] == 2

    response = client.patch(f"/api/savings-goals/{goal['id']}", json={"target_date": None})
    assert response.status_code == 200
    assert response.json()["target_date"] is None

    response = client.patch(f"/api/savings-goals/{goal['id']}", json={"active": False})
    assert response.status_code == 200
    assert response.json()["active"] is False


def test_savings_goal_validation_and_missing_record(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    payload = {
        "name": "Holiday",
        "target_amount_cents": 200000,
        "current_balance_cents": 0,
        "priority": 100,
    }
    for invalid in (
        {**payload, "name": "   "},
        {**payload, "target_amount_cents": -1},
        {**payload, "current_balance_cents": -1},
        {**payload, "priority": -1},
        {**payload, "target_date": "not-a-date"},
    ):
        response = client.post("/api/savings-goals", json=invalid)
        assert response.status_code == 422
    assert client.patch("/api/savings-goals/9999", json={"name": "Missing"}).status_code == 404
    assert client.patch("/api/savings-goals/9999", json={}).status_code == 422


def test_savings_page_is_available(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    response = client.get("/savings")
    assert response.status_code == 200
    assert "Savings Goals" in response.text
    assert "/api/savings-goals" in response.text



def test_undated_general_savings_uses_weekly_amount_and_carries_missed_weeks(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 10000 WHERE id = 1"
        )

    created = client.post("/api/savings-goals", json={
        "name": "General savings",
        "current_balance_cents": 0,
        "weekly_target_cents": 2500,
        "priority": 1,
    })
    assert created.status_code == 201, created.text
    goal = created.json()
    assert goal["target_amount_cents"] == 0
    assert goal["target_date"] is None
    assert goal["weekly_target_cents"] == 2500
    assert "contribution_per_period_cents" not in goal

    first = client.get("/api/cash-flow/weekly", params={"forecast_weeks": 1})
    assert first.status_code == 200, first.text
    current_plan = first.json()["savings_plan"][0]
    assert current_plan["goal_name"] == "General savings"
    assert current_plan["planned_this_week_cents"] == 2500
    assert current_plan["funded_this_week_cents"] == 2500
    assert current_plan["target_date"] is None
    assert current_plan["remaining_cents"] is None

    next_start = date.fromisoformat(first.json()["end_date"]) + timedelta(days=1)
    next_end = next_start + timedelta(days=6)
    second = client.get("/api/cash-flow/weekly", params={
        "start_date": next_start.isoformat(),
        "end_date": next_end.isoformat(),
        "forecast_weeks": 1,
    })
    assert second.status_code == 200, second.text
    next_plan = second.json()["savings_plan"][0]
    assert next_plan["arrears_cents"] == 2500
    assert next_plan["planned_this_week_cents"] == 5000


def test_savings_page_uses_weekly_not_per_payday_language(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    response = client.get("/savings")
    assert response.status_code == 200
    assert "Weekly savings amount" in response.text
    assert "Legacy per-payday amount" not in response.text
    assert "per payday" not in response.text

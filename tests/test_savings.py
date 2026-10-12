"""Integration tests for persisted savings-goal management."""
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

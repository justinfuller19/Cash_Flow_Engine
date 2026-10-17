"""Tests for deleting user-created CashFlow Engine records."""
from fastapi.testclient import TestClient

from app import database
from app.main import app


def client_for(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "delete-records.db"))
    database.initialize_database()
    return TestClient(app)


def test_delete_endpoints_return_not_found_for_unknown_ids(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    endpoints = [
        "/api/people/99999",
        "/api/income-sources/99999",
        "/api/bills/99999",
        "/api/debts/99999",
        "/api/savings-goals/99999",
        "/api/transactions/99999",
        "/api/plans/99999",
    ]
    for endpoint in endpoints:
        response = client.delete(endpoint)
        assert response.status_code == 404, (endpoint, response.text)


def test_deleting_household_member_also_deletes_income_sources(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    person = client.post(
        "/api/people",
        json={"name": "Delete Test"},
    )
    assert person.status_code == 201, person.text
    income = client.post(
        "/api/income-sources",
        json={
            "person_id": person.json()["id"],
            "name": "Test paycheck",
            "net_amount_cents": 100000,
            "frequency": "biweekly",
            "next_payday": "2026-10-16",
        },
    )
    assert income.status_code == 201, income.text
    deleted = client.delete(f"/api/people/{person.json()['id']}")
    assert deleted.status_code == 200, deleted.text
    assert client.get("/api/people").json() == []
    assert client.get("/api/income-sources").json() == []


def test_management_pages_show_delete_controls(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    assert "Delete member" in client.get("/setup").text
    assert "Delete income" in client.get("/setup").text
    assert 'Delete "+(kind==="bills"?"bill":"debt")' in client.get("/bills").text
    assert "Delete goal" in client.get("/savings").text
    assert 'el("button","edit","Delete")' in client.get("/transactions").text
    assert "Remove plan" in client.get("/planner").text

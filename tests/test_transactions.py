"""Integration tests for the manual transaction ledger."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import database
from app.main import app


def client_for(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "transactions.db"))
    database.initialize_database()
    return TestClient(app)


def test_create_list_filter_and_edit_transactions(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    today = date.today()
    income = client.post(
        "/api/transactions",
        json={
            "transaction_date": today.isoformat(),
            "category": " Income ",
            "description": "Paycheck",
            "amount_cents": 150000,
            "account_name": "Checking",
        },
    )
    assert income.status_code == 201, income.text
    assert income.json()["category"] == "income"
    assert income.json()["amount_cents"] == 150000

    expense = client.post(
        "/api/transactions",
        json={
            "transaction_date": (today - timedelta(days=2)).isoformat(),
            "category": "Food",
            "description": "Groceries",
            "amount_cents": -12500,
            "notes": "Weekly shopping",
        },
    )
    assert expense.status_code == 201, expense.text
    assert expense.json()["amount_cents"] == -12500
    assert len(client.get("/api/transactions").json()) == 2
    filtered = client.get(
        "/api/transactions",
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
    )
    assert len(filtered.json()) == 1
    assert filtered.json()[0]["description"] == "Paycheck"

    updated = client.patch(
        f"/api/transactions/{expense.json()['id']}",
        json={"description": "Grocery store", "amount_cents": -13000, "account_name": "Card", "notes": None},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["description"] == "Grocery store"
    assert updated.json()["amount_cents"] == -13000
    assert updated.json()["account_name"] == "Card"
    assert updated.json()["notes"] is None


def test_transaction_validation_and_missing_records(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    today = date.today().isoformat()
    payload = {
        "transaction_date": today,
        "category": "other",
        "description": "Something",
        "amount_cents": 100,
    }
    for invalid in (
        {**payload, "category": " "},
        {**payload, "description": " "},
        {**payload, "transaction_date": "not-a-date"},
    ):
        assert client.post("/api/transactions", json=invalid).status_code == 422
    assert client.get(
        "/api/transactions",
        params={"start_date": "2026-12-31", "end_date": "2026-01-01"},
    ).status_code == 422
    assert client.patch("/api/transactions/9999", json={"description": "Missing"}).status_code == 404
    created = client.post("/api/transactions", json=payload).json()
    assert client.patch(
        f"/api/transactions/{created['id']}", json={"description": None}
    ).status_code == 422


def test_transactions_page_and_dashboard_month_totals(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    today = date.today().isoformat()
    client.post(
        "/api/transactions",
        json={"transaction_date": today, "category": "income", "description": "Pay",
              "amount_cents": 200000},
    )
    client.post(
        "/api/transactions",
        json={"transaction_date": today, "category": "food", "description": "Food",
              "amount_cents": -25000},
    )
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["month_income_cents"] == 200000
    assert dashboard["month_expenses_cents"] == 25000
    assert dashboard["recent_transactions"][0]["description"] in {"Pay", "Food"}
    page = client.get("/transactions")
    assert page.status_code == 200
    assert "Transactions" in page.text
    assert "/api/transactions" in page.text

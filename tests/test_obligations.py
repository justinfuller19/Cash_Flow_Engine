"""Integration tests for persisted bill and debt management."""
import sqlite3

from fastapi.testclient import TestClient

from app import database
from app.main import app


def setup_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "obligations.db"))
    database.initialize_database()
    return TestClient(app)


def test_bill_create_list_update_and_deactivate(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    created = client.post(
        "/api/bills",
        json={
            "name": "Electric",
            "amount_cents": 14567,
            "next_due_date": "2026-11-18",
            "frequency": "monthly",
            "category": "utilities",
            "flexible_window_days": 3,
            "autopay": True,
        },
    )
    assert created.status_code == 201, created.text
    bill = created.json()
    assert bill["amount_cents"] == 14567
    assert bill["autopay"] is True
    assert bill["active"] is True
    assert client.get("/api/bills").json()[0]["id"] == bill["id"]

    updated = client.patch(
        f"/api/bills/{bill['id']}",
        json={"amount_cents": 15500, "autopay": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["amount_cents"] == 15500
    assert updated.json()["autopay"] is False

    inactive = client.patch(f"/api/bills/{bill['id']}", json={"active": False})
    assert inactive.status_code == 200
    assert inactive.json()["active"] is False


def test_bill_validation_and_missing_record(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    bad_payloads = [
        {"name": " ", "amount_cents": 1, "next_due_date": "2026-11-18"},
        {"name": "Electric", "amount_cents": -1, "next_due_date": "2026-11-18"},
        {"name": "Electric", "amount_cents": 1, "next_due_date": "not-a-date"},
        {"name": "Electric", "amount_cents": 1, "next_due_date": "2026-11-18",
         "frequency": "sometimes"},
    ]
    for payload in bad_payloads:
        assert client.post("/api/bills", json=payload).status_code == 422
    assert client.patch("/api/bills/9999", json={"name": "Missing"}).status_code == 404
    assert client.patch("/api/bills/9999", json={"active": None}).status_code == 422


def test_debt_create_update_and_deactivate(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    created = client.post(
        "/api/debts",
        json={
            "name": "Travel card",
            "balance_cents": 230045,
            "minimum_payment_cents": 8500,
            "apr_percent": 24.99,
            "next_due_date": "2026-11-22",
        },
    )
    assert created.status_code == 201, created.text
    debt = created.json()
    assert debt["balance_cents"] == 230045
    assert debt["minimum_payment_cents"] == 8500
    assert debt["due_day_of_month"] == 22
    assert client.get("/api/debts").json()[0]["id"] == debt["id"]

    updated = client.patch(
        f"/api/debts/{debt['id']}",
        json={"balance_cents": 215000, "minimum_payment_cents": 7500},
    )
    assert updated.status_code == 200
    assert updated.json()["balance_cents"] == 215000
    assert updated.json()["minimum_payment_cents"] == 7500

    inactive = client.patch(f"/api/debts/{debt['id']}", json={"active": False})
    assert inactive.status_code == 200
    assert inactive.json()["active"] is False


def test_debt_validation_and_missing_record(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    payload = {
        "name": "Card",
        "balance_cents": 10000,
        "minimum_payment_cents": 500,
        "apr_percent": 19.9,
        "next_due_date": "2026-11-22",
    }
    for invalid in (
        {**payload, "balance_cents": -1},
        {**payload, "minimum_payment_cents": -1},
        {**payload, "apr_percent": -0.1},
        {**payload, "apr_percent": 1001},
        {**payload, "next_due_date": "not-a-date"},
    ):
        assert client.post("/api/debts", json=invalid).status_code == 422
    assert client.patch("/api/debts/9999", json={"name": "Missing"}).status_code == 404
    assert client.patch("/api/debts/9999", json={"active": None}).status_code == 422


def test_bills_page_and_schema_migration(tmp_path, monkeypatch):
    path = tmp_path / "legacy-v2.db"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(path))
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE persons (
             id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
             default_buffer_cents INTEGER NOT NULL DEFAULT 0,
             active INTEGER NOT NULL DEFAULT 1,
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
    )
    connection.execute(
        """CREATE TABLE bills (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             name TEXT NOT NULL, amount_cents INTEGER NOT NULL,
             due_day_of_month INTEGER, next_due_date TEXT,
             flexible_window_days INTEGER NOT NULL DEFAULT 0,
             active INTEGER NOT NULL DEFAULT 1,
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
    )
    connection.execute(
        """CREATE TABLE credit_cards (
             id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
             balance_cents INTEGER NOT NULL DEFAULT 0,
             minimum_payment_cents INTEGER NOT NULL DEFAULT 0,
             due_day_of_month INTEGER, apr_percent REAL NOT NULL DEFAULT 0,
             active INTEGER NOT NULL DEFAULT 1,
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
    )
    connection.execute("INSERT INTO bills (name, amount_cents) VALUES ('Old bill', 1000)")
    connection.commit()
    connection.close()

    database.initialize_database()
    with database.connect() as db:
        bill_columns = {row["name"] for row in db.execute("PRAGMA table_info(bills)")}
        debt_columns = {row["name"] for row in db.execute("PRAGMA table_info(credit_cards)")}
        old_bill = db.execute("SELECT category, frequency, autopay FROM bills").fetchone()
        version = db.execute("PRAGMA user_version").fetchone()[0]
    assert {"category", "frequency", "autopay"} <= bill_columns
    assert "next_due_date" in debt_columns
    assert tuple(old_bill) == ("other", "monthly", 0)
    assert version == database.SCHEMA_VERSION

    client = TestClient(app)
    response = client.get("/bills")
    assert response.status_code == 200
    assert "Recurring bills" in response.text
    assert "Credit cards & debts" in response.text

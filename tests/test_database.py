from datetime import date

from fastapi.testclient import TestClient

from app import database
from app.main import app


def test_database_initializes_expected_tables(tmp_path, monkeypatch):
    path = tmp_path / "test-cashflow.db"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(path))

    database.initialize_database()

    with database.connect() as db:
        names = {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert {"persons", "income_sources", "bills", "credit_cards",
            "savings_accounts", "savings_goals", "transactions",
            "payday_plans", "plan_items"} <= names


def test_person_and_income_source_endpoints_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "api-test.db"))
    database.initialize_database()
    client = TestClient(app)

    person_response = client.post(
        "/api/people",
        json={"name": "Household member", "default_buffer_cents": 25000},
    )
    assert person_response.status_code == 201
    person = person_response.json()
    assert person["name"] == "Household member"
    assert person["default_buffer_cents"] == 25000

    income_response = client.post(
        "/api/income-sources",
        json={
            "person_id": person["id"],
            "name": "Paycheck",
            "net_amount_cents": 140000,
            "frequency": "biweekly",
            "next_payday": "2026-11-13",
        },
    )
    assert income_response.status_code == 201
    assert income_response.json()["net_amount_cents"] == 140000

    assert client.get("/api/people").json()[0]["id"] == person["id"]
    assert client.get("/api/income-sources").json()[0]["person_id"] == person["id"]


def test_income_source_requires_existing_person(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "foreign-key-test.db"))
    database.initialize_database()
    client = TestClient(app)

    response = client.post(
        "/api/income-sources",
        json={
            "person_id": 999,
            "name": "Paycheck",
            "net_amount_cents": 100000,
            "frequency": "weekly",
            "next_payday": "2026-11-13",
        },
    )
    assert response.status_code == 404

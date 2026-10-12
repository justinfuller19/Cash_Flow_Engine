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


def _seed_person_and_income(client):
    person = client.post(
        "/api/people", json={"name": "Member", "default_buffer_cents": 10000}
    ).json()
    income = client.post(
        "/api/income-sources",
        json={
            "person_id": person["id"],
            "name": "Paycheck",
            "net_amount_cents": 140000,
            "frequency": "biweekly",
            "next_payday": "2026-11-13",
        },
    ).json()
    return person, income


def test_update_person_edits_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "edit-person.db"))
    database.initialize_database()
    client = TestClient(app)
    person, _ = _seed_person_and_income(client)

    response = client.patch(
        f"/api/people/{person['id']}",
        json={"name": "Renamed", "default_buffer_cents": 25000},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Renamed"
    assert updated["default_buffer_cents"] == 25000
    assert updated["active"] is True

    # Unchanged fields are preserved on partial updates.
    response = client.patch(
        f"/api/people/{person['id']}", json={"default_buffer_cents": 30000}
    )
    assert response.json()["name"] == "Renamed"


def test_update_person_deactivate_and_reactivate(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "deactivate-person.db"))
    database.initialize_database()
    client = TestClient(app)
    person, _ = _seed_person_and_income(client)

    response = client.patch(
        f"/api/people/{person['id']}", json={"active": False}
    )
    assert response.status_code == 200
    assert response.json()["active"] is False

    listed = client.get("/api/people").json()
    assert listed[0]["active"] is False

    response = client.patch(f"/api/people/{person['id']}", json={"active": True})
    assert response.json()["active"] is True


def test_update_person_validates_input(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "validate-person.db"))
    database.initialize_database()
    client = TestClient(app)
    person, _ = _seed_person_and_income(client)

    assert client.patch("/api/people/9999", json={"name": "Ghost"}).status_code == 404
    assert (
        client.patch(f"/api/people/{person['id']}", json={"name": "   "}).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/people/{person['id']}", json={"default_buffer_cents": -1}
        ).status_code
        == 422
    )
    assert client.patch(f"/api/people/{person['id']}", json={}).status_code == 422


def test_update_income_source_edits_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "edit-income.db"))
    database.initialize_database()
    client = TestClient(app)
    _, income = _seed_person_and_income(client)

    response = client.patch(
        f"/api/income-sources/{income['id']}",
        json={
            "name": "New job",
            "net_amount_cents": 200000,
            "frequency": "monthly",
            "next_payday": "2026-12-01",
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "New job"
    assert updated["net_amount_cents"] == 200000
    assert updated["frequency"] == "monthly"
    assert updated["next_payday"] == "2026-12-01"


def test_update_income_source_deactivate_and_reassign_person(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "deactivate-income.db"))
    database.initialize_database()
    client = TestClient(app)
    person, income = _seed_person_and_income(client)
    other = client.post("/api/people", json={"name": "Other"}).json()

    response = client.patch(
        f"/api/income-sources/{income['id']}",
        json={"active": False, "person_id": other["id"]},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["active"] is False
    assert updated["person_id"] == other["id"]

    response = client.patch(
        f"/api/income-sources/{income['id']}", json={"active": True}
    )
    assert response.json()["active"] is True


def test_update_income_source_validates_input(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "validate-income.db"))
    database.initialize_database()
    client = TestClient(app)
    _, income = _seed_person_and_income(client)

    assert client.patch("/api/income-sources/9999", json={"name": "Ghost"}).status_code == 404
    assert (
        client.patch(
            f"/api/income-sources/{income['id']}", json={"person_id": 9999}
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/income-sources/{income['id']}", json={"name": "   "}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/income-sources/{income['id']}", json={"net_amount_cents": -5}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/income-sources/{income['id']}", json={"frequency": "sometimes"}
        ).status_code
        == 422
    )
    assert client.patch(f"/api/income-sources/{income['id']}", json={}).status_code == 422


def test_migration_adds_active_to_legacy_persons_table(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "legacy.db"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(path))

    # Simulate a database created before the active flag existed (schema v1).
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            default_buffer_cents INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        "INSERT INTO persons (name, default_buffer_cents) VALUES ('Legacy', 5000)"
    )
    connection.commit()
    connection.close()

    database.initialize_database()

    with database.connect() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(persons)")}
        assert "active" in columns
        row = db.execute("SELECT active FROM persons WHERE name = 'Legacy'").fetchone()
        assert row["active"] == 1
        version = db.execute("PRAGMA user_version").fetchone()[0]
        assert version == database.SCHEMA_VERSION

    # The migrated database serves the new field through the API.
    client = TestClient(app)
    assert client.get("/api/people").json()[0]["active"] is True

"""Tests for safe backup download and guarded household-data reset."""
import sqlite3

from fastapi.testclient import TestClient

from app import database
from app.main import app


def setup_client(tmp_path, monkeypatch):
    db_path = tmp_path / "cashflow.db"
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(db_path))
    monkeypatch.setenv("CASHFLOW_BACKUP_DIR", str(backup_dir))
    database.initialize_database()
    return TestClient(app), db_path, backup_dir


def seed_financial_data(client):
    person = client.post("/api/people", json={"name": "Member"}).json()
    income = client.post(
        "/api/income-sources",
        json={
            "person_id": person["id"],
            "name": "Paycheck",
            "net_amount_cents": 100000,
            "frequency": "weekly",
            "next_payday": "2026-10-19",
        },
    )
    assert income.status_code == 201, income.text
    transaction = client.post(
        "/api/transactions",
        json={
            "transaction_date": "2026-10-17",
            "transaction_type": "expense",
            "category": "groceries",
            "description": "Grocery run",
            "amount_cents": -4500,
        },
    )
    assert transaction.status_code == 201, transaction.text
    client.patch(
        "/api/household/settings",
        json={"buffer_target_cents": 20000, "starting_cash_cents": 80000},
    )


def test_backup_endpoint_returns_a_database_download(tmp_path, monkeypatch):
    client, db_path, backup_dir = setup_client(tmp_path, monkeypatch)
    seed_financial_data(client)
    response = client.get("/api/settings-data/backup")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-type"] == "application/octet-stream"
    assert list(backup_dir.glob("cashflow-*.db"))
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_clear_all_data_requires_exact_phrase_and_preserves_a_verified_backup(tmp_path, monkeypatch):
    client, _db_path, backup_dir = setup_client(tmp_path, monkeypatch)
    seed_financial_data(client)
    original_categories = client.get("/api/categories").json()

    rejected = client.post(
        "/api/settings-data/clear-all-data",
        json={"confirmation_phrase": "delete all data"},
    )
    assert rejected.status_code == 422
    assert not list(backup_dir.glob("cashflow-*.db"))
    assert len(client.get("/api/people").json()) == 1

    response = client.post(
        "/api/settings-data/clear-all-data",
        json={"confirmation_phrase": "DELETE ALL DATA"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["cleared"] is True
    assert result["backup_created"] is True
    assert result["categories_preserved"] is True
    backup_path = backup_dir / result["backup_file"]
    assert backup_path.is_file()

    # The backup contains the records from before the destructive reset.
    with sqlite3.connect(backup_path) as db:
        assert db.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # The live database is empty but still has its schema and household settings row.
    assert client.get("/api/people").json() == []
    assert client.get("/api/income-sources").json() == []
    assert client.get("/api/transactions").json() == []
    settings = client.get("/api/household/settings").json()
    assert settings["starting_cash_cents"] == 0
    assert "buffer_target_cents" not in settings
    assert client.get("/api/categories").json() == original_categories
    with sqlite3.connect(_db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM payday_plans").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM plan_items").fetchone()[0] == 0
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_settings_data_page_explains_backup_and_confirmation(tmp_path, monkeypatch):
    client, _db_path, _backup_dir = setup_client(tmp_path, monkeypatch)
    response = client.get("/settings-data")
    assert response.status_code == 200
    assert "DELETE ALL DATA" in response.text
    assert "Create & download backup" in response.text
    assert "backup" in response.text.lower()


def test_household_two_backup_and_reset_back_up_the_active_database(tmp_path, monkeypatch):
    client, _db_path, backup_dir = setup_client(tmp_path, monkeypatch)
    seed_financial_data(client)

    # Register a second household on the same server and give it different data.
    response = client.post("/register", data={
        "household_name": "Second Household",
        "username": "second-household@example.test",
        "password": "second-household-password-123",
    }, follow_redirects=False)
    assert response.status_code == 303
    person = client.post("/api/people", json={"name": "Second household member"})
    assert person.status_code == 201

    # A manual backup must contain household 2's data, not household 1's data.
    response = client.get("/api/settings-data/backup")
    assert response.status_code == 200
    manual_backup = next(backup_dir.glob("cashflow-*.db"))
    with sqlite3.connect(manual_backup) as db:
        names = [row[0] for row in db.execute("SELECT name FROM persons")]
        assert names == ["Second household member"]

    # The safety backup made before Clear All Data must use the same tenant DB.
    response = client.post("/api/settings-data/clear-all-data", json={
        "confirmation_phrase": "DELETE ALL DATA",
    })
    assert response.status_code == 200, response.text
    reset_backup = backup_dir / response.json()["backup_file"]
    with sqlite3.connect(reset_backup) as db:
        names = [row[0] for row in db.execute("SELECT name FROM persons")]
        assert names == ["Second household member"]
    assert client.get("/api/people").json() == []

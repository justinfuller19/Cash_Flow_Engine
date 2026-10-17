"""Household-wide cash buffer target and starting cash balance (schema v6)."""
import sqlite3

from fastapi.testclient import TestClient

from app import database
from app.main import app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "household.db"))
    database.initialize_database()
    return TestClient(app)


def test_fresh_database_has_zeroed_household_settings(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    settings = client.get("/api/household/settings").json()
    assert settings["buffer_target_cents"] == 0
    assert settings["starting_cash_cents"] == 0


def test_update_household_settings(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    updated = client.patch(
        "/api/household/settings",
        json={"buffer_target_cents": 20000, "starting_cash_cents": 80000},
    ).json()
    assert updated["buffer_target_cents"] == 20000
    assert updated["starting_cash_cents"] == 80000
    assert client.get("/api/household/settings").json() == updated


def test_partial_update_keeps_other_field(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.patch("/api/household/settings", json={"buffer_target_cents": 15000})
    settings = client.get("/api/household/settings").json()
    assert settings["buffer_target_cents"] == 15000
    assert settings["starting_cash_cents"] == 0


def test_update_rejects_negative_and_null_and_empty(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert (
        client.patch("/api/household/settings", json={"buffer_target_cents": -1}).status_code
        == 422
    )
    assert (
        client.patch("/api/household/settings", json={"starting_cash_cents": -50}).status_code
        == 422
    )
    assert (
        client.patch("/api/household/settings", json={"buffer_target_cents": None}).status_code
        == 422
    )
    assert client.patch("/api/household/settings", json={}).status_code == 422


def test_migration_seeds_buffer_from_active_person_buffers(tmp_path, monkeypatch):
    path = tmp_path / "v5.db"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(path))

    # Simulate a schema-v5 database with per-person buffers.
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            default_buffer_cents INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.executemany(
        "INSERT INTO persons (name, default_buffer_cents, active) VALUES (?, ?, ?)",
        [("Ana", 12000, 1), ("Ben", 8000, 1), ("Gone", 50000, 0)],
    )
    connection.execute("PRAGMA user_version = 5")
    connection.commit()
    connection.close()

    database.initialize_database()

    with database.connect() as db:
        row = db.execute(
            "SELECT buffer_target_cents, starting_cash_cents FROM household_settings WHERE id = 1"
        ).fetchone()
        # Only active members count toward the seeded household target.
        assert row["buffer_target_cents"] == 20000
        assert row["starting_cash_cents"] == 0
        version = db.execute("PRAGMA user_version").fetchone()[0]
        assert version == database.SCHEMA_VERSION


def test_generated_plan_uses_single_household_buffer(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    person = client.post("/api/people", json={"name": "Member"}).json()
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
    client.patch("/api/household/settings", json={"buffer_target_cents": 20000})

    plan = client.post(
        "/api/plans/generate",
        json={
            "plan_date": "2026-11-21",
            "period_start": "2026-11-21",
            "period_end": "2026-11-27",
            "starting_cash_cents": 50000,
            "debt_strategy": "avalanche",
        },
    ).json()

    buffer_items = [item for item in plan["items"] if item["category"] == "buffer"]
    assert len(buffer_items) == 1
    assert buffer_items[0]["name"] == "Household cash buffer"
    assert buffer_items[0]["requested_cents"] == 20000
    assert buffer_items[0]["funded_cents"] == 20000


def test_dashboard_reports_household_buffer(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/people", json={"name": "Member"})
    client.patch("/api/household/settings", json={"buffer_target_cents": 20000})
    summary = client.get("/api/dashboard").json()
    assert summary["protected_buffers_cents"] == 20000

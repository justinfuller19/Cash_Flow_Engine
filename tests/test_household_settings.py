"""Household-wide cash buffer target and starting cash balance (schema v6)."""
import sqlite3
from datetime import date, timedelta

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
    assert settings["starting_cash_cents"] == 0
    assert settings["suggested_cash_balance_cents"] is None


def test_update_household_settings(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    updated = client.patch(
        "/api/household/settings",
        json={"starting_cash_cents": 80000},
    ).json()
    assert updated["starting_cash_cents"] == 80000
    assert client.get("/api/household/settings").json() == updated


def test_partial_update_keeps_other_field(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.patch("/api/household/settings", json={"starting_cash_cents": 15000})
    settings = client.get("/api/household/settings").json()
    assert settings["starting_cash_cents"] == 15000


def test_update_rejects_negative_and_null_and_empty(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert (
        client.patch("/api/household/settings", json={"starting_cash_cents": -50}).status_code
        == 422
    )
    assert client.patch("/api/household/settings", json={}).status_code == 422


def test_migration_preserves_legacy_buffer_column_from_active_person_buffers(tmp_path, monkeypatch):
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


def test_generated_plan_does_not_reserve_a_household_buffer(tmp_path, monkeypatch):
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

    assert all(item["category"] != "buffer" for item in plan["items"])
    assert plan["unallocated_cents"] == 150000


def test_dashboard_reports_daily_cash_forecast(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/people", json={"name": "Member"})
    summary = client.get("/api/dashboard").json()
    assert "projected_cash_shortfall_cents" in summary
    assert "lowest_projected_cash_cents" in summary



def _current_week_start():
    today = date.today()
    return today - timedelta(days=(today.weekday() - 4) % 7)


def test_cash_snapshot_is_dated_and_reported(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    effective_week = _current_week_start()
    updated = client.patch(
        "/api/household/settings",
        json={
            "starting_cash_cents": 82500,
            "starting_cash_effective_week": effective_week.isoformat(),
        },
    )
    assert updated.status_code == 200
    settings = updated.json()
    assert settings["starting_cash_cents"] == 82500
    assert settings["starting_cash_effective_week"] == effective_week.isoformat()
    assert settings["starting_cash_confirmed_at"]
    assert settings["starting_cash_is_stale"] is False

    with database.connect() as db:
        rows = db.execute(
            "SELECT effective_week_start, balance_cents FROM cash_balance_snapshots"
        ).fetchall()
    assert [(row["effective_week_start"], row["balance_cents"]) for row in rows] == [
        (effective_week.isoformat(), 82500)
    ]


def test_cash_snapshot_requires_financial_week_start_date(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    invalid_date = _current_week_start() + timedelta(days=1)
    response = client.patch(
        "/api/household/settings",
        json={
            "starting_cash_cents": 10000,
            "starting_cash_effective_week": invalid_date.isoformat(),
        },
    )
    assert response.status_code == 422
    assert "first day of the configured financial week" in response.json()["detail"]


def test_cash_snapshot_history_does_not_replace_current_balance(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    current_week = _current_week_start()
    prior_week = current_week - timedelta(days=7)
    current = client.patch(
        "/api/household/settings",
        json={
            "starting_cash_cents": 80000,
            "starting_cash_effective_week": current_week.isoformat(),
        },
    )
    assert current.status_code == 200
    historical = client.patch(
        "/api/household/settings",
        json={
            "starting_cash_cents": 50000,
            "starting_cash_effective_week": prior_week.isoformat(),
        },
    )
    assert historical.status_code == 200
    assert historical.json()["starting_cash_cents"] == 80000

    report = client.get(
        "/api/cash-flow/weekly",
        params={
            "start_date": prior_week.isoformat(),
            "end_date": (prior_week + timedelta(days=6)).isoformat(),
        },
    )
    assert report.status_code == 200
    assert report.json()["starting_cash_cents"] == 50000

    with database.connect() as db:
        count = db.execute("SELECT COUNT(*) FROM cash_balance_snapshots").fetchone()[0]
    assert count == 2


def test_stale_cash_snapshot_is_flagged(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    effective_week = _current_week_start()
    client.patch(
        "/api/household/settings",
        json={
            "starting_cash_cents": 25000,
            "starting_cash_effective_week": effective_week.isoformat(),
        },
    )
    with database.connect() as db:
        db.execute(
            "UPDATE cash_balance_snapshots SET confirmed_at = ? WHERE effective_week_start = ?",
            ((date.today() - timedelta(days=11)).isoformat() + " 12:00:00", effective_week.isoformat()),
        )
    settings = client.get("/api/household/settings").json()
    assert settings["starting_cash_is_stale"] is True
    report = client.get("/api/cash-flow/weekly").json()
    assert any("WARNING:" in note and "more than 10 days ago" in note for note in report["notes"])


def test_migration_seeds_existing_nonzero_starting_cash(tmp_path, monkeypatch):
    path = tmp_path / "legacy-v16.db"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(path))
    database.initialize_database()
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 45678 WHERE id = 1"
        )
        db.execute("DELETE FROM cash_balance_snapshots")
        db.execute("PRAGMA user_version = 16")

    database.initialize_database()
    with database.connect() as db:
        snapshot = db.execute(
            "SELECT effective_week_start, balance_cents FROM cash_balance_snapshots"
        ).fetchone()
        assert snapshot["effective_week_start"] == _current_week_start().isoformat()
        assert snapshot["balance_cents"] == 45678
        assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION



def test_snapshot_week_counts_only_transactions_after_week_start_once(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    effective_week = _current_week_start()
    client.patch(
        "/api/household/settings",
        json={
            "starting_cash_cents": 100000,
            "starting_cash_effective_week": effective_week.isoformat(),
        },
    )
    before = client.post(
        "/api/transactions",
        json={
            "transaction_date": (effective_week - timedelta(days=1)).isoformat(),
            "category": "groceries",
            "transaction_type": "expense",
            "description": "Before snapshot",
            "amount_cents": -7000,
        },
    )
    after = client.post(
        "/api/transactions",
        json={
            "transaction_date": effective_week.isoformat(),
            "category": "groceries",
            "transaction_type": "expense",
            "description": "After snapshot",
            "amount_cents": -15000,
        },
    )
    assert before.status_code == 201
    assert after.status_code == 201

    report = client.get(
        "/api/cash-flow/weekly",
        params={"forecast_weeks": 2},
    )
    assert report.status_code == 200
    data = report.json()
    assert data["starting_cash_cents"] == 100000
    assert data["transaction_expenses_cents"] == 15000
    assert data["projected_ending_cash_cents"] == 85000
    assert data["future_forecast"][0]["starting_cash_cents"] == 85000
    assert data["future_forecast"][0]["projected_ending_cash_cents"] == 85000
    assert data["free_to_spend_cents"] == 85000



def test_cash_snapshot_rejects_future_financial_week(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    future_week = _current_week_start() + timedelta(days=7)
    response = client.patch(
        "/api/household/settings",
        json={
            "starting_cash_cents": 90000,
            "starting_cash_effective_week": future_week.isoformat(),
        },
    )
    assert response.status_code == 422
    assert "current or a past financial week" in response.json()["detail"]
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM cash_balance_snapshots").fetchone()[0] == 0



def test_suggested_cash_balance_uses_only_transactions_before_current_week(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    current_week = _current_week_start()
    prior_week = current_week - timedelta(days=7)
    saved = client.patch(
        "/api/household/settings",
        json={
            "starting_cash_cents": 100000,
            "starting_cash_effective_week": prior_week.isoformat(),
        },
    )
    assert saved.status_code == 200

    before_snapshot = client.post(
        "/api/transactions",
        json={
            "transaction_date": (prior_week - timedelta(days=1)).isoformat(),
            "category": "groceries",
            "transaction_type": "expense",
            "description": "Before confirmed snapshot",
            "amount_cents": -7000,
        },
    )
    on_snapshot_day = client.post(
        "/api/transactions",
        json={
            "transaction_date": prior_week.isoformat(),
            "category": "groceries",
            "transaction_type": "expense",
            "description": "After opening snapshot",
            "amount_cents": -1000,
        },
    )
    later_transaction = client.post(
        "/api/transactions",
        json={
            "transaction_date": (current_week - timedelta(days=1)).isoformat(),
            "category": "groceries",
            "transaction_type": "expense",
            "description": "Before current week",
            "amount_cents": -1500,
        },
    )
    assert before_snapshot.status_code == 201
    assert on_snapshot_day.status_code == 201
    assert later_transaction.status_code == 201

    transfer = client.post(
        "/api/transactions",
        json={
            "transaction_date": (prior_week + timedelta(days=2)).isoformat(),
            "category": "account transfer",
            "transaction_type": "transfer",
            "description": "Move between household accounts",
            "amount_cents": 30000,
        },
    )
    assert transfer.status_code == 201

    settings = client.get("/api/household/settings").json()
    assert settings["suggested_cash_balance_cents"] == 97500
    assert settings["suggested_cash_effective_week"] == current_week.isoformat()
    assert settings["suggested_cash_based_on_effective_week"] == prior_week.isoformat()
    # Only income and expense change the estimate; transfers between household
    # accounts are cash-neutral and excluded from both the amount and count.
    assert settings["suggested_cash_transaction_count"] == 2
    # The estimate is advisory and must not mutate the confirmed historical snapshot.
    assert settings["starting_cash_cents"] == 100000


def test_weekly_report_exposes_cash_confirmation_control(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/weekly")
    assert response.status_code == 200
    assert 'id="weekly-cash-form"' in response.text
    assert 'id="weekly-cash-use-estimate"' in response.text
    assert "Use estimated balance (not saved)" in response.text

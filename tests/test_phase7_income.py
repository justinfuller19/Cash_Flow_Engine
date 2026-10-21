"""Regression tests for Phase 7 income schedule changes."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import database
from app.main import app


def setup_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "phase7.db"))
    database.initialize_database()
    return TestClient(app)


def test_income_end_date_stops_future_occurrences_and_can_be_cleared(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    person = client.post("/api/people", json={"name": "Household member"}).json()
    response = client.post("/api/income-sources", json={
        "person_id": person["id"], "name": "Contract work",
        "net_amount_cents": 50000, "frequency": "weekly",
        "next_payday": "2026-10-19", "end_date": "2026-10-26",
    })
    assert response.status_code == 201, response.text
    income = response.json()
    assert income["end_date"] == "2026-10-26"

    first = client.get("/api/cash-flow/weekly", params={
        "start_date": "2026-10-19", "end_date": "2026-10-25",
    })
    assert first.status_code == 200, first.text
    assert first.json()["expected_income_cents"] == 50000

    after_end = client.get("/api/cash-flow/weekly", params={
        "start_date": "2026-10-27", "end_date": "2026-11-02",
    })
    assert after_end.status_code == 200, after_end.text
    assert after_end.json()["expected_income_cents"] == 0

    cleared = client.patch(f"/api/income-sources/{income['id']}", json={"end_date": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["end_date"] is None


def test_temporary_income_override_applies_only_to_matching_paydays(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    person = client.post("/api/people", json={"name": "Household member"}).json()
    income = client.post("/api/income-sources", json={
        "person_id": person["id"], "name": "Paycheck",
        "net_amount_cents": 50000, "frequency": "weekly",
        "next_payday": "2026-10-19",
    }).json()

    override = client.post(f"/api/income-sources/{income['id']}/amount-overrides", json={
        "start_date": "2026-10-19", "end_date": "2026-10-19",
        "amount_cents": 75000, "reason": "One-time bonus",
    })
    assert override.status_code == 201, override.text

    week = client.get("/api/cash-flow/weekly", params={
        "start_date": "2026-10-19", "end_date": "2026-10-25",
    })
    assert week.status_code == 200, week.text
    assert week.json()["expected_income_cents"] == 75000
    assert week.json()["expected_income"][0]["amount_cents"] == 75000

    next_week = client.get("/api/cash-flow/weekly", params={
        "start_date": "2026-10-26", "end_date": "2026-11-01",
    })
    assert next_week.status_code == 200, next_week.text
    assert next_week.json()["expected_income_cents"] == 50000


def test_overrides_reject_overlapping_ranges_and_invalid_dates(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    person = client.post("/api/people", json={"name": "Household member"}).json()
    income = client.post("/api/income-sources", json={
        "person_id": person["id"], "name": "Paycheck",
        "net_amount_cents": 50000, "frequency": "weekly",
        "next_payday": "2026-10-19",
    }).json()
    endpoint = f"/api/income-sources/{income['id']}/amount-overrides"
    first = client.post(endpoint, json={
        "start_date": "2026-10-19", "end_date": "2026-11-02",
        "amount_cents": 40000,
    })
    assert first.status_code == 201, first.text
    overlap = client.post(endpoint, json={
        "start_date": "2026-10-26", "end_date": "2026-11-09",
        "amount_cents": 30000,
    })
    assert overlap.status_code == 409
    invalid = client.post(endpoint, json={
        "start_date": "2026-11-02", "end_date": "2026-10-19",
        "amount_cents": 30000,
    })
    assert invalid.status_code == 422




def test_dashboard_paydays_respect_end_dates_and_amount_overrides(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    person = client.post("/api/people", json={"name": "Household member"}).json()
    next_payday = date.today() + timedelta(days=7)

    ended = client.post("/api/income-sources", json={
        "person_id": person["id"], "name": "Ended contract",
        "net_amount_cents": 50000, "frequency": "weekly",
        "next_payday": next_payday.isoformat(),
        "end_date": date.today().isoformat(),
    })
    assert ended.status_code == 201, ended.text

    ongoing = client.post("/api/income-sources", json={
        "person_id": person["id"], "name": "Temporary reduced paycheck",
        "net_amount_cents": 200000, "frequency": "weekly",
        "next_payday": next_payday.isoformat(),
        "end_date": (next_payday + timedelta(days=7)).isoformat(),
    })
    assert ongoing.status_code == 201, ongoing.text
    override = client.post(
        f"/api/income-sources/{ongoing.json()['id']}/amount-overrides",
        json={
            "start_date": next_payday.isoformat(),
            "end_date": next_payday.isoformat(),
            "amount_cents": 150000,
            "reason": "Temporary reduction",
        },
    )
    assert override.status_code == 201, override.text

    response = client.get("/api/dashboard")
    assert response.status_code == 200, response.text
    paydays = response.json()["next_paydays"]
    assert all("Ended contract" not in item["name"] for item in paydays)
    temporary = next(item for item in paydays if "Temporary reduced paycheck" in item["name"])
    assert temporary["date"] == next_payday.isoformat()
    assert temporary["amount_cents"] == 150000


def test_v19_migration_preserves_income_and_adds_override_storage(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "phase7.db"
    client = setup_client(tmp_path, monkeypatch)
    person = client.post("/api/people", json={"name": "Member"}).json()
    created = client.post("/api/income-sources", json={
        "person_id": person["id"], "name": "Weekly pay",
        "net_amount_cents": 123456, "frequency": "weekly",
        "next_payday": "2026-10-23",
    })
    assert created.status_code == 201, created.text
    income_id = created.json()["id"]

    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE income_amount_overrides")
        db.execute("ALTER TABLE income_sources RENAME TO income_sources_v20")
        db.execute(
            """CREATE TABLE income_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
                name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
                net_amount_cents INTEGER NOT NULL CHECK(net_amount_cents >= 0),
                frequency TEXT NOT NULL CHECK(frequency IN
                    ('weekly', 'biweekly', 'semimonthly', 'twice_monthly_last',
                     'first_third_friday', 'last_business_day', 'monthly', 'irregular')),
                next_payday TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        db.execute(
            """INSERT INTO income_sources
               (id, person_id, name, net_amount_cents, frequency, next_payday, active, created_at)
               SELECT id, person_id, name, net_amount_cents, frequency, next_payday, active, created_at
               FROM income_sources_v20"""
        )
        db.execute("DROP TABLE income_sources_v20")
        db.execute("PRAGMA user_version = 19")

    database.initialize_database()
    with database.connect() as db:
        row = db.execute("SELECT * FROM income_sources WHERE id = ?", (income_id,)).fetchone()
        assert row["name"] == "Weekly pay"
        assert row["net_amount_cents"] == 123456
        assert row["next_payday"] == "2026-10-23"
        assert row["end_date"] is None
        assert db.execute("SELECT COUNT(*) FROM income_amount_overrides").fetchone()[0] == 0
        assert db.execute("PRAGMA user_version").fetchone()[0] == 20

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

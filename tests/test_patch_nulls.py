"""Regression tests for explicit nulls in PATCH requests."""
from fastapi.testclient import TestClient

from app import database
from app.main import app


def test_patch_person_rejects_explicit_null_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "null-person.db"))
    database.initialize_database()
    client = TestClient(app)
    person = client.post("/api/people", json={"name": "Member"}).json()

    for payload in (
        {"name": None},
        {"active": None},
    ):
        response = client.patch(f"/api/people/{person['id']}", json=payload)
        assert response.status_code == 422, (payload, response.text)


def test_patch_household_settings_rejects_explicit_null_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "null-settings.db"))
    database.initialize_database()
    client = TestClient(app)

    for payload in (
        {"buffer_target_cents": None},
        {"starting_cash_cents": None},
    ):
        response = client.patch("/api/household/settings", json=payload)
        assert response.status_code == 422, (payload, response.text)


def test_patch_income_source_rejects_explicit_null_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "null-income.db"))
    database.initialize_database()
    client = TestClient(app)
    person = client.post("/api/people", json={"name": "Member"}).json()
    income = client.post(
        "/api/income-sources",
        json={
            "person_id": person["id"],
            "name": "Paycheck",
            "net_amount_cents": 100000,
            "frequency": "biweekly",
            "next_payday": "2026-11-13",
        },
    ).json()

    for payload in (
        {"person_id": None},
        {"name": None},
        {"net_amount_cents": None},
        {"frequency": None},
        {"next_payday": None},
        {"active": None},
    ):
        response = client.patch(
            f"/api/income-sources/{income['id']}", json=payload
        )
        assert response.status_code == 422, (payload, response.text)

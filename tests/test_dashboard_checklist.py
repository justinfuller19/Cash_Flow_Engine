"""Integration tests for the live dashboard and action checklist."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import database
from app.main import app


def seed_planned_household(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "dashboard.db"))
    database.initialize_database()
    client = TestClient(app)
    today = date.today()
    iso = lambda value: value.isoformat()
    person = client.post("/api/people", json={"name": "Member"}).json()
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    client.patch("/api/household/settings", json={
        "buffer_target_cents": 10000, "financial_week_start": weekdays[today.weekday()]
    })
    client.post(
        "/api/income-sources",
        json={
            "person_id": person["id"], "name": "Paycheck",
            "net_amount_cents": 100000, "frequency": "weekly",
            "next_payday": iso(today + timedelta(days=1)),
        },
    )
    client.post(
        "/api/bills",
        json={
            "name": "Electric", "amount_cents": 25000,
            "next_due_date": iso(today + timedelta(days=2)),
            "frequency": "monthly", "category": "utilities",
        },
    )
    client.post(
        "/api/debts",
        json={
            "name": "Card", "balance_cents": 80000,
            "minimum_payment_cents": 5000, "apr_percent": 24.9,
            "next_due_date": iso(today + timedelta(days=3)),
        },
    )
    client.post(
        "/api/savings-goals",
        json={
            "name": "Emergency fund", "target_amount_cents": 100000,
            "current_balance_cents": 0, "contribution_per_period_cents": 10000,
            "priority": 1,
        },
    )
    return client, None, today


def test_checklist_records_status_actual_amount_and_date(tmp_path, monkeypatch):
    client, plan, today = seed_planned_household(tmp_path, monkeypatch)
    checklist = client.get("/api/checklist")
    assert checklist.status_code == 200
    assert len(checklist.json()) == 4
    assert {item["category"] for item in checklist.json()} == {"income", "bill", "debt", "savings"}
    item = checklist.json()[0]

    response = client.patch(
        f"/api/plan-items/{item['id']}",
        json={
            "status": "completed",
            "actual_amount_cents": 9000,
            "actual_date": today.isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["status"] == "completed"
    assert updated["actual_amount_cents"] == 9000
    assert updated["actual_date"] == today.isoformat()

    assert len(client.get("/api/checklist").json()) == 3
    auto_plan = next(p for p in client.get("/api/plans").json() if p.get("is_auto_generated"))
    saved = client.get(f"/api/plans/{auto_plan['id']}").json()
    saved_item = next(row for row in saved["items"] if row["id"] == item["id"])
    assert saved_item["is_completed"] is True
    assert saved_item["status"] == "completed"
    assert saved_item["actual_amount_cents"] == 9000


def test_checklist_savings_actual_updates_goal_and_carryover_history(tmp_path, monkeypatch):
    client, _, today = seed_planned_household(tmp_path, monkeypatch)
    checklist = client.get("/api/checklist").json()
    savings_item = next(item for item in checklist if item["category"] == "savings")
    response = client.patch(
        f"/api/plan-items/{savings_item['id']}",
        json={"status": "completed", "actual_amount_cents": 4000, "actual_date": today.isoformat()},
    )
    assert response.status_code == 200, response.text
    with database.connect() as db:
        goal = db.execute("SELECT current_balance_cents FROM savings_goals WHERE name = 'Emergency fund'").fetchone()
        transactions = db.execute(
            "SELECT COUNT(*) FROM transactions WHERE category = 'savings contribution'"
        ).fetchone()[0]
        allocations = db.execute(
            "SELECT COUNT(*) FROM surplus_allocations WHERE allocation_type = 'savings' AND status = 'recorded'"
        ).fetchone()[0]
    assert goal["current_balance_cents"] == 4000
    assert transactions == 1
    assert allocations == 1


def test_checklist_validation_and_page(tmp_path, monkeypatch):
    client, _, _ = seed_planned_household(tmp_path, monkeypatch)
    item = client.get("/api/checklist").json()[0]
    assert client.patch(f"/api/plan-items/{item['id']}", json={"status": None}).status_code == 422
    assert client.patch(f"/api/plan-items/{item['id']}", json={"actual_amount_cents": -1}).status_code == 422
    assert client.patch("/api/plan-items/9999", json={"status": "completed"}).status_code == 404
    page = client.get("/checklist")
    assert page.status_code == 200
    assert "Payday Checklist" in page.text
    assert "generated automatically" in page.text
    assert 'id="checklist-pagination"' in page.text
    assert "const PAGE_SIZE=10" in page.text


def test_dashboard_summarizes_saved_household_and_plan(tmp_path, monkeypatch):
    client, plan, today = seed_planned_household(tmp_path, monkeypatch)
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert data["active_household_members"] == 1
    assert data["protected_buffers_cents"] == 10000
    assert data["pending_checklist_count"] == 4
    assert data["next_paydays"][0]["date"] == (today + timedelta(days=1)).isoformat()
    assert any(bill["name"] == "Electric" for bill in data["upcoming_bills"])
    assert any(debt["name"] == "Card" for debt in data["upcoming_debts"])
    assert data["savings_goals"][0]["name"] == "Emergency fund"
    assert any(row.get("is_auto_generated") for row in client.get("/api/plans").json())


def test_schema_v5_adds_checklist_actual_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "schema-v5.db"))
    database.initialize_database()
    with database.connect() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(plan_items)")}
        version = db.execute("PRAGMA user_version").fetchone()[0]
    assert {"status", "actual_amount_cents", "actual_date"} <= columns
    assert version == database.SCHEMA_VERSION

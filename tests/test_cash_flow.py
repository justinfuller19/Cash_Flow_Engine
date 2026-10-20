"""Integration tests for the weekly cash-flow forecast and transaction reconciliation."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import database
from app.main import app


def setup_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "weekly-cash-flow.db"))
    database.initialize_database()
    return TestClient(app)


def test_weekly_forecast_reconciles_linked_income_and_bill_transactions(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    start = date(2026, 10, 19)
    end = start + timedelta(days=6)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 20000 WHERE id = 1"
        )
        person_id = db.execute(
            "INSERT INTO persons (name) VALUES ('Household')"
        ).lastrowid
        income_id = db.execute(
            """INSERT INTO income_sources
               (person_id, name, net_amount_cents, frequency, next_payday, active)
               VALUES (?, 'Paycheck', 50000, 'weekly', ?, 1)""",
            (person_id, start.isoformat()),
        ).lastrowid
        bill_id = db.execute(
            """INSERT INTO bills
               (name, amount_cents, due_day_of_month, next_due_date, flexible_window_days,
                active, category, frequency, autopay)
               VALUES ('Electric', 30000, 19, ?, 0, 1, 'utilities', 'monthly', 0)""",
            (start.isoformat(),),
        ).lastrowid
        db.execute(
            """INSERT INTO credit_cards
               (name, balance_cents, minimum_payment_cents, due_day_of_month,
                apr_percent, active, next_due_date)
               VALUES ('Card', 500000, 10000, 21, 19.9, 1, ?)""",
            ((start + timedelta(days=2)).isoformat(),),
        )
        db.execute(
            """INSERT INTO savings_goals
               (name, target_amount_cents, current_balance_cents, weekly_target_cents)
               VALUES ('Emergency savings', 100000, 20000, 5000)"""
        )

    transactions = [
        {
            "transaction_date": start.isoformat(),
            "transaction_type": "income",
            "linked_obligation_type": "income",
            "linked_obligation_id": income_id,
            "category": "paychecks",
            "description": "Paycheck received",
            "amount_cents": 50000,
        },
        {
            "transaction_date": start.isoformat(),
            "transaction_type": "expense",
            "linked_obligation_type": "bill",
            "linked_obligation_id": bill_id,
            "category": "utilities",
            "description": "Electric paid",
            "amount_cents": -30000,
        },
        {
            "transaction_date": (start + timedelta(days=1)).isoformat(),
            "transaction_type": "expense",
            "category": "groceries",
            "description": "Groceries",
            "amount_cents": -4000,
        },
        {
            "transaction_date": (start + timedelta(days=1)).isoformat(),
            "transaction_type": "transfer",
            "category": "account transfer",
            "description": "Checking to savings",
            "amount_cents": 20000,
        },
    ]
    for transaction in transactions:
        response = client.post("/api/transactions", json=transaction)
        assert response.status_code == 201, response.text

    response = client.get(
        "/api/cash-flow/weekly",
        params={"start_date": start.isoformat(), "end_date": end.isoformat()},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["starting_cash_cents"] == 100000
    assert report["expected_income_cents"] == 0
    assert report["recorded_income_cents"] == 50000
    assert report["covered_income"][0]["covered_by_transaction_id"]
    assert report["bills_due_cents"] == 0
    assert report["bills_covered_by_transactions_cents"] == 30000
    assert report["minimum_debt_payments_cents"] == 10000
    assert report["transaction_expenses_cents"] == 34000
    assert report["category_spending"] == [
        {"category": "utilities", "amount_cents": 30000},
        {"category": "groceries", "amount_cents": 4000},
    ]
    assert report["planned_savings_cents"] == 5000
    assert report["funded_savings_cents"] == 5000
    assert report["projected_cash_before_buffer_cents"] == 106000
    assert report["buffer_replenishment_cents"] == 0
    assert report["free_to_spend_cents"] == 81000
    assert report["extra_debt_payoff_capacity_cents"] == 81000
    assert len(report["transfers"]) == 1


def test_weekly_forecast_flags_shortfalls_and_validates_window(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 1000, buffer_target_cents = 5000 WHERE id = 1"
        )
        db.execute(
            """INSERT INTO bills
               (name, amount_cents, next_due_date, flexible_window_days, active,
                category, frequency, autopay)
               VALUES ('Rent', 20000, '2026-10-19', 0, 1, 'housing', 'monthly', 0)"""
        )

    response = client.get(
        "/api/cash-flow/weekly",
        params={"start_date": "2026-10-19", "end_date": "2026-10-25"},
    )
    assert response.status_code == 200
    report = response.json()
    assert report["projected_cash_before_buffer_cents"] == -19000
    assert report["projected_shortfall_cents"] == 19000
    assert report["buffer_shortfall_cents"] == 24000
    assert report["free_to_spend_cents"] == 0

    assert client.get(
        "/api/cash-flow/weekly",
        params={"start_date": "2026-10-25", "end_date": "2026-10-19"},
    ).status_code == 422
    assert client.get(
        "/api/cash-flow/weekly",
        params={"start_date": "2026-10-01", "end_date": "2026-11-02"},
    ).status_code == 422


def test_transaction_link_validation_rejects_mismatched_types(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
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
    ).json()
    payload = {
        "transaction_date": "2026-10-19",
        "transaction_type": "expense",
        "category": "groceries",
        "description": "Bad link",
        "amount_cents": -1000,
        "linked_obligation_type": "income",
        "linked_obligation_id": income["id"],
    }
    assert client.post("/api/transactions", json=payload).status_code == 422
    assert client.post(
        "/api/transactions",
        json={**payload, "linked_obligation_type": "bill", "linked_obligation_id": None},
    ).status_code == 422


def test_weekly_report_page_and_dashboard_summary_are_available(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    report_page = client.get("/weekly")
    assert report_page.status_code == 200
    assert "Weekly Cash Flow" in report_page.text
    assert 'id="start"' in report_page.text
    assert 'id="end"' in report_page.text
    assert "/api/cash-flow/weekly" in report_page.text
    assert 'id="category-spending"' in report_page.text

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert 'id="weekly-summary"' in dashboard.text
    assert 'href="/weekly"' in dashboard.text
    assert 'id="weekly-category-spending"' in dashboard.text



def test_future_rent_reserves_current_surplus_and_flags_buffer_shortfall(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 20000 WHERE id = 1"
        )
        db.execute(
            """INSERT INTO bills
               (name, amount_cents, next_due_date, flexible_window_days, active,
                category, frequency, autopay)
               VALUES ('Rent', 90000, '2026-10-26', 0, 1, 'housing', 'monthly', 0)"""
        )

    response = client.get(
        "/api/cash-flow/weekly",
        params={"start_date": "2026-10-19", "end_date": "2026-10-25", "forecast_weeks": 2},
    )
    assert response.status_code == 200, response.text
    report = response.json()

    # Without lookahead, this week would incorrectly show $800 as spendable.
    assert report["free_to_spend_before_future_reserve_cents"] == 80000
    # Next week's $900 rent leaves only $100, below the $200 household buffer.
    assert report["future_forecast"][0]["starting_cash_cents"] == 100000
    assert report["future_forecast"][0]["projected_ending_cash_cents"] == 10000
    assert report["future_forecast"][0]["buffer_shortfall_cents"] == 10000
    assert report["future_buffer_shortfall_cents"] == 10000
    assert report["free_to_spend_cents"] == 0
    assert report["extra_debt_payoff_capacity_cents"] == 0
    assert report["future_buffer_reservation_cents"] == 80000


def test_future_forecast_carries_ending_cash_and_limits_safe_spending(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 150000, buffer_target_cents = 20000 WHERE id = 1"
        )
        db.execute(
            """INSERT INTO bills
               (name, amount_cents, next_due_date, flexible_window_days, active,
                category, frequency, autopay)
               VALUES ('Rent', 90000, '2026-10-26', 0, 1, 'housing', 'monthly', 0)"""
        )

    response = client.get(
        "/api/cash-flow/weekly",
        params={"start_date": "2026-10-19", "end_date": "2026-10-25", "forecast_weeks": 2},
    )
    assert response.status_code == 200, response.text
    report = response.json()

    # $1,500 cash less $900 rent leaves $600; only $400 is above the buffer.
    assert report["future_forecast"][0]["starting_cash_cents"] == 150000
    assert report["future_forecast"][0]["projected_ending_cash_cents"] == 60000
    assert report["free_to_spend_cents"] == 40000
    assert report["future_buffer_reservation_cents"] == 90000


def test_early_linked_paycheck_within_seven_days_is_not_double_counted(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    scheduled_payday = date(2026, 10, 24)
    recorded_payday = scheduled_payday - timedelta(days=5)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 0, buffer_target_cents = 0 WHERE id = 1"
        )
        person_id = db.execute(
            "INSERT INTO persons (name) VALUES ('Household')"
        ).lastrowid
        income_id = db.execute(
            """INSERT INTO income_sources
               (person_id, name, net_amount_cents, frequency, next_payday, active)
               VALUES (?, 'Paycheck', 50000, 'weekly', ?, 1)""",
            (person_id, scheduled_payday.isoformat()),
        ).lastrowid

    response = client.post("/api/transactions", json={
        "transaction_date": recorded_payday.isoformat(),
        "transaction_type": "income",
        "linked_obligation_type": "income",
        "linked_obligation_id": income_id,
        "category": "paychecks",
        "description": "Paycheck received early",
        "amount_cents": 50000,
    })
    assert response.status_code == 201, response.text

    response = client.get(
        "/api/cash-flow/weekly",
        params={"start_date": recorded_payday.isoformat(),
                "end_date": (recorded_payday + timedelta(days=6)).isoformat(),
                "forecast_weeks": 1},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["recorded_income_cents"] == 50000
    assert report["expected_income_cents"] == 0
    assert report["covered_income"][0]["covered_by_transaction_id"]


def test_dashboard_explains_future_buffer_reservation(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    response = client.get("/")
    assert response.status_code == 200
    assert "future_buffer_reservation_cents" in response.text
    assert "reserved for upcoming bills to protect the household buffer." in response.text

def test_household_financial_week_start_defaults_to_friday_and_is_editable(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)

    settings = client.get("/api/household/settings")
    assert settings.status_code == 200
    assert settings.json()["financial_week_start"] == "Friday"

    updated = client.patch(
        "/api/household/settings",
        json={"financial_week_start": "Monday"},
    )
    assert updated.status_code == 200
    assert updated.json()["financial_week_start"] == "Monday"


def test_weekly_forecast_defaults_to_configured_financial_week(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET financial_week_start = 'Monday' WHERE id = 1"
        )

    response = client.get("/api/cash-flow/weekly")
    assert response.status_code == 200
    report = response.json()
    today = date.today()
    expected_start = today - timedelta(days=today.weekday())

    assert report["financial_week_start"] == "Monday"
    assert report["start_date"] == expected_start.isoformat()
    assert report["end_date"] == (expected_start + timedelta(days=6)).isoformat()

def test_safe_surplus_allocation_is_reserved_and_can_be_cancelled(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 20000"
        )
        debt_id = db.execute(
            """INSERT INTO credit_cards
               (name, balance_cents, minimum_payment_cents, due_day_of_month, apr_percent, active, next_due_date)
               VALUES ('Smallest card', 50000, 5000, 25, 19.9, 1, ?)""",
            (date.today().isoformat(),),
        ).lastrowid

    before = client.get("/api/cash-flow/weekly").json()
    assert before["free_to_spend_cents"] >= 10000
    created = client.post(
        "/api/surplus-allocations",
        json={
            "allocation_type": "debt",
            "amount_cents": 10000,
            "target_debt_id": debt_id,
            "description": "Snowball payment",
        },
    )
    assert created.status_code == 201
    allocation_id = created.json()["id"]

    after = client.get("/api/cash-flow/weekly").json()
    assert after["planned_allocations_cents"] == 10000
    assert after["free_to_spend_cents"] == before["free_to_spend_cents"] - 10000

    cancelled = client.post(f"/api/surplus-allocations/{allocation_id}/cancel", json={})
    assert cancelled.status_code == 200
    released = client.get("/api/cash-flow/weekly").json()
    assert released["planned_allocations_cents"] == 0


def test_record_actual_debt_payment_updates_balance_and_transaction_ledger(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 20000"
        )
        debt_id = db.execute(
            """INSERT INTO credit_cards
               (name, balance_cents, minimum_payment_cents, due_day_of_month, apr_percent, active, next_due_date)
               VALUES ('Smallest card', 50000, 5000, 25, 19.9, 1, ?)""",
            (date.today().isoformat(),),
        ).lastrowid

    allocation = client.post(
        "/api/surplus-allocations",
        json={"allocation_type": "debt", "amount_cents": 10000, "target_debt_id": debt_id},
    )
    assert allocation.status_code == 201
    allocation_id = allocation.json()["id"]

    recorded = client.post(
        f"/api/surplus-allocations/{allocation_id}/record-debt-payment",
        json={"actual_amount_cents": 8000, "description": "Actual card payment"},
    )
    assert recorded.status_code == 200
    assert recorded.json()["actual_amount_cents"] == 8000
    with database.connect() as db:
        debt = db.execute("SELECT balance_cents FROM credit_cards WHERE id = ?", (debt_id,)).fetchone()
        transaction = db.execute(
            "SELECT transaction_type, linked_obligation_type, linked_obligation_id, amount_cents FROM transactions WHERE id = ?",
            (recorded.json()["transaction_id"],),
        ).fetchone()
    assert debt["balance_cents"] == 42000
    assert transaction["transaction_type"] == "expense"
    assert transaction["linked_obligation_type"] == "debt"
    assert transaction["linked_obligation_id"] == debt_id
    assert transaction["amount_cents"] == 8000

    # The debt link improves traceability, but an extra principal payment must
    # not satisfy the scheduled minimum-payment cycle.
    report = client.get("/api/cash-flow/weekly", params={
        "start_date": date.today().isoformat(),
        "end_date": (date.today() + timedelta(days=6)).isoformat(),
    }).json()
    minimum = next(item for item in report["minimum_debt_payments"] if item["id"] == debt_id)
    assert minimum["qualifying_payments_cents"] == 0
    assert minimum["amount_cents"] == 5000

def test_surplus_allocation_rejects_amount_above_safe_forecast(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 25000, buffer_target_cents = 20000"
        )
    response = client.post(
        "/api/surplus-allocations",
        json={"allocation_type": "buffer", "amount_cents": 10000},
    )
    assert response.status_code == 422

def test_record_actual_savings_contribution_updates_goal_and_ledger(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 20000"
        )
        goal_id = db.execute(
            """INSERT INTO savings_goals
               (name, target_amount_cents, current_balance_cents, weekly_target_cents)
               VALUES ('Vacation', 100000, 10000, 0)"""
        ).lastrowid

    allocation = client.post(
        "/api/surplus-allocations",
        json={"allocation_type": "savings", "amount_cents": 12000, "savings_goal_id": goal_id},
    )
    assert allocation.status_code == 201
    allocation_id = allocation.json()["id"]

    recorded = client.post(
        f"/api/surplus-allocations/{allocation_id}/record-savings-contribution",
        json={"actual_amount_cents": 9000, "description": "Vacation savings"},
    )
    assert recorded.status_code == 200
    with database.connect() as db:
        goal = db.execute("SELECT current_balance_cents FROM savings_goals WHERE id = ?", (goal_id,)).fetchone()
        transaction = db.execute("SELECT transaction_type, amount_cents FROM transactions WHERE id = ?", (recorded.json()["transaction_id"],)).fetchone()
    assert goal["current_balance_cents"] == 19000
    assert transaction["transaction_type"] == "expense"
    assert transaction["amount_cents"] == 9000


def test_old_buffer_allocations_do_not_reserve_new_financial_weeks(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 20000, financial_week_start = 'Friday'"
        )
        db.execute(
            """INSERT INTO surplus_allocations
               (allocation_date, allocation_type, amount_cents, description)
               VALUES ('2020-01-03', 'buffer', 15000, 'Prior week spending buffer')"""
        )
    report = client.get("/api/cash-flow/weekly").json()
    assert report["planned_allocations_cents"] == 0


def test_dated_savings_goal_uses_weekly_target_and_carries_shortfall(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    today = date.today()
    start = today - timedelta(days=(today.weekday() - 4) % 7)  # default Friday week
    deadline = start + timedelta(days=28)  # five weekly targets including this week
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 10000 WHERE id = 1"
        )

    created = client.post(
        "/api/savings-goals",
        json={
            "name": "Trip",
            "target_amount_cents": 50000,
            "current_balance_cents": 0,
            "target_date": deadline.isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    goal = created.json()
    assert goal["weekly_target_cents"] == 10000
    first = client.get(
        "/api/cash-flow/weekly",
        params={"start_date": start.isoformat(), "end_date": (start + timedelta(days=6)).isoformat(), "forecast_weeks": 1},
    ).json()
    assert first["planned_savings_cents"] == 10000
    assert first["savings_plan"][0]["arrears_cents"] == 0

    allocation = client.post(
        "/api/surplus-allocations",
        json={"allocation_type": "savings", "amount_cents": 4000, "savings_goal_id": goal["id"]},
    )
    assert allocation.status_code == 201, allocation.text
    recorded = client.post(
        f"/api/surplus-allocations/{allocation.json()['id']}/record-savings-contribution",
        json={"actual_amount_cents": 4000},
    )
    assert recorded.status_code == 200, recorded.text

    second_start = start + timedelta(days=7)
    second = client.get(
        "/api/cash-flow/weekly",
        params={"start_date": second_start.isoformat(), "end_date": (second_start + timedelta(days=6)).isoformat(), "forecast_weeks": 1},
    ).json()
    plan = second["savings_plan"][0]
    assert plan["arrears_cents"] == 6000
    assert plan["planned_this_week_cents"] == 16000
    assert plan["funded_this_week_cents"] <= plan["planned_this_week_cents"]



def _insert_ongoing_savings_goal(db, name, schedule_start, weekly_target_cents=10000):
    return db.execute(
        """INSERT INTO savings_goals
           (name, target_amount_cents, current_balance_cents,
            weekly_target_cents, schedule_start_date)
           VALUES (?, 0, 0, ?, ?)""",
        (name, weekly_target_cents, schedule_start.isoformat()),
    ).lastrowid


def test_existing_savings_arrears_are_reserved_once_across_future_weeks(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    today = date.today()
    start = today - timedelta(days=(today.weekday() - 4) % 7)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 0, financial_week_start = 'Friday' WHERE id = 1"
        )
        _insert_ongoing_savings_goal(db, "Emergency fund", start - timedelta(days=14))

    report = client.get(
        "/api/cash-flow/weekly",
        params={
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=6)).isoformat(),
            "forecast_weeks": 3,
        },
    ).json()

    plan = report["savings_plan"][0]
    assert plan["regular_target_cents"] == 10000
    assert plan["outstanding_arrears_cents"] == 20000
    assert plan["catch_up_cents"] == 20000
    assert plan["planned_this_week_cents"] == 30000
    assert plan["funded_catch_up_cents"] == 20000

    future = report["future_forecast"]
    assert [week["savings_arrears_cents"] for week in future] == [20000, 20000]
    assert [week["planned_savings_cents"] for week in future] == [10000, 10000]
    assert [week["funded_savings_cents"] for week in future] == [10000, 10000]
    # The $20 arrears are reserved in the current week, not deducted again
    # from each future week's projected cash.
    assert report["lowest_projected_cash_cents"] == 50000
    assert report["free_to_spend_cents"] == 50000
    assert any("catch-up amounts already reserved" in note.lower() for note in report["notes"])


def test_recorded_savings_contribution_reduces_arrears_once(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    today = date.today()
    start = today - timedelta(days=(today.weekday() - 4) % 7)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 500000, buffer_target_cents = 0, financial_week_start = 'Friday' WHERE id = 1"
        )
        goal_id = _insert_ongoing_savings_goal(
            db, "Emergency fund", start - timedelta(days=14)
        )

    allocation = client.post(
        "/api/surplus-allocations",
        json={
            "allocation_type": "savings",
            "amount_cents": 5000,
            "savings_goal_id": goal_id,
            "description": "Catch up savings",
        },
    )
    assert allocation.status_code == 201, allocation.text
    recorded = client.post(
        f"/api/surplus-allocations/{allocation.json()['id']}/record-savings-contribution",
        json={"actual_amount_cents": 5000, "transaction_date": today.isoformat()},
    )
    assert recorded.status_code == 200, recorded.text

    report = client.get(
        "/api/cash-flow/weekly",
        params={
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=6)).isoformat(),
            "forecast_weeks": 3,
        },
    ).json()

    plan = report["savings_plan"][0]
    assert plan["recorded_this_week_cents"] == 5000
    assert plan["outstanding_arrears_cents"] == 20000
    assert plan["arrears_after_recorded_contributions_cents"] == 15000
    assert plan["catch_up_cents"] == 15000
    assert plan["planned_this_week_cents"] == 25000
    assert plan["funded_catch_up_cents"] == 15000
    assert [week["savings_arrears_cents"] for week in report["future_forecast"]] == [15000, 15000]
    assert [week["planned_savings_cents"] for week in report["future_forecast"]] == [10000, 10000]
    assert report["lowest_projected_cash_cents"] == 450000
    assert report["free_to_spend_cents"] == 450000


def test_unfunded_future_savings_targets_do_not_create_future_arrears(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    today = date.today()
    start = today - timedelta(days=(today.weekday() - 4) % 7)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 25000, buffer_target_cents = 0, financial_week_start = 'Friday' WHERE id = 1"
        )
        _insert_ongoing_savings_goal(db, "Emergency fund", start - timedelta(days=7))

    report = client.get(
        "/api/cash-flow/weekly",
        params={
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=6)).isoformat(),
            "forecast_weeks": 3,
        },
    ).json()

    future = report["future_forecast"]
    assert [week["savings_arrears_cents"] for week in future] == [10000, 10000]
    assert [week["planned_savings_cents"] for week in future] == [10000, 10000]
    assert [week["funded_savings_cents"] for week in future] == [5000, 0]
    assert [week["savings_shortfall_cents"] for week in future] == [5000, 10000]
    assert report["lowest_projected_cash_cents"] == 0
    assert report["free_to_spend_cents"] == 0


def test_changing_household_buffer_recalculates_weekly_savings_funding(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    today = date.today()
    start = today - timedelta(days=(today.weekday() - 4) % 7)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 50000, buffer_target_cents = 40000 WHERE id = 1"
        )
    created = client.post(
        "/api/savings-goals",
        json={
            "name": "Holiday",
            "target_amount_cents": 100000,
            "current_balance_cents": 0,
            "target_date": (start + timedelta(days=7)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    params = {
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=6)).isoformat(),
        "forecast_weeks": 1,
    }
    before = client.get("/api/cash-flow/weekly", params=params).json()
    assert before["funded_savings_cents"] == 10000

    changed = client.patch(
        "/api/household/settings",
        json={"buffer_target_cents": 20000},
    )
    assert changed.status_code == 200, changed.text
    after = client.get("/api/cash-flow/weekly", params=params).json()
    assert after["funded_savings_cents"] == 30000
    assert after["savings_plan"][0]["funded_this_week_cents"] == 30000


def test_debt_minimum_uses_balance_cap_when_balance_is_below_stated_minimum(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    start = date(2026, 10, 19)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 0 WHERE id = 1"
        )
        db.execute(
            """INSERT INTO credit_cards
               (name, balance_cents, minimum_payment_cents, due_day_of_month,
                apr_percent, active, next_due_date)
               VALUES ('Nearly paid card', 6000, 10000, 21, 19.9, 1, ?)""",
            ((start + timedelta(days=2)).isoformat(),),
        )

    report = client.get(
        "/api/cash-flow/weekly",
        params={"start_date": start.isoformat(), "end_date": (start + timedelta(days=6)).isoformat()},
    ).json()

    debt = report["minimum_debt_payments"][0]
    assert debt["original_minimum_payment_cents"] == 10000
    assert debt["balance_capped_minimum_cents"] == 6000
    assert debt["amount_cents"] == 6000
    assert report["minimum_debt_payments_cents"] == 6000


def test_debt_minimum_subtracts_multiple_linked_partial_payments_once(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    start = date(2026, 10, 19)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 0 WHERE id = 1"
        )
        debt_id = db.execute(
            """INSERT INTO credit_cards
               (name, balance_cents, minimum_payment_cents, due_day_of_month,
                apr_percent, active, next_due_date)
               VALUES ('Partial card', 50000, 10000, 21, 19.9, 1, ?)""",
            ((start + timedelta(days=2)).isoformat(),),
        ).lastrowid

    transaction_ids = []
    for day, amount in ((start, 2500), (start + timedelta(days=2), 2000)):
        response = client.post("/api/transactions", json={
            "transaction_date": day.isoformat(),
            "transaction_type": "expense",
            "linked_obligation_type": "debt",
            "linked_obligation_id": debt_id,
            "category": "other",
            "description": "Partial card payment",
            "amount_cents": -amount,
        })
        assert response.status_code == 201, response.text
        transaction_ids.append(response.json()["id"])

    report = client.get(
        "/api/cash-flow/weekly",
        params={"start_date": start.isoformat(), "end_date": (start + timedelta(days=6)).isoformat()},
    ).json()
    debt = report["minimum_debt_payments"][0]

    assert debt["qualifying_payments_cents"] == 4500
    assert debt["paid_toward_minimum_cents"] == 4500
    assert debt["amount_cents"] == 5500
    assert debt["status"] == "partial"
    assert set(debt["covered_by_transaction_ids"]) == set(transaction_ids)
    assert report["minimum_debt_payments_cents"] == 5500
    assert report["debt_payments_covered_by_transactions_cents"] == 4500
    # Actual payment expenses plus the remaining minimum equal the original minimum,
    # rather than charging the payment and the full minimum a second time.
    assert report["transaction_expenses_cents"] + report["minimum_debt_payments_cents"] == 10000


def test_debt_overpayment_satisfies_minimum_and_reports_extra_reduction(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    start = date(2026, 10, 19)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 0 WHERE id = 1"
        )
        debt_id = db.execute(
            """INSERT INTO credit_cards
               (name, balance_cents, minimum_payment_cents, due_day_of_month,
                apr_percent, active, next_due_date)
               VALUES ('Overpaid card', 50000, 10000, 21, 19.9, 1, ?)""",
            ((start + timedelta(days=2)).isoformat(),),
        ).lastrowid

    payment = client.post("/api/transactions", json={
        "transaction_date": (start + timedelta(days=2)).isoformat(),
        "transaction_type": "expense",
        "linked_obligation_type": "debt",
        "linked_obligation_id": debt_id,
        "category": "other",
        "description": "Payment above minimum",
        "amount_cents": -12500,
    })
    assert payment.status_code == 201, payment.text

    report = client.get(
        "/api/cash-flow/weekly",
        params={"start_date": start.isoformat(), "end_date": (start + timedelta(days=6)).isoformat()},
    ).json()
    debt = report["minimum_debt_payments"][0]

    assert debt["amount_cents"] == 0
    assert debt["status"] == "recorded"
    assert debt["paid_toward_minimum_cents"] == 10000
    assert debt["extra_payment_cents"] == 2500
    assert report["minimum_debt_payments_cents"] == 0
    assert report["debt_payments_covered_by_transactions_cents"] == 10000
    assert report["debt_extra_payments_by_transactions_cents"] == 2500
    assert report["transaction_expenses_cents"] == 12500


def test_debt_payment_link_does_not_cover_another_debt_or_wrong_cycle(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    start = date(2026, 10, 1)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 0 WHERE id = 1"
        )
        matched_debt_id = db.execute(
            """INSERT INTO credit_cards
               (name, balance_cents, minimum_payment_cents, due_day_of_month,
                apr_percent, active, next_due_date)
               VALUES ('Linked card', 50000, 10000, 5, 19.9, 1, '2026-10-05')"""
        ).lastrowid
        other_debt_id = db.execute(
            """INSERT INTO credit_cards
               (name, balance_cents, minimum_payment_cents, due_day_of_month,
                apr_percent, active, next_due_date)
               VALUES ('Other card', 50000, 8000, 20, 19.9, 1, '2026-10-20')"""
        ).lastrowid

    # This is linked to the first debt but is 15 days after its due date, so it
    # cannot satisfy that cycle. It must not be credited to the other debt either.
    payment = client.post("/api/transactions", json={
        "transaction_date": "2026-10-20",
        "transaction_type": "expense",
        "linked_obligation_type": "debt",
        "linked_obligation_id": matched_debt_id,
        "category": "other",
        "description": "Late unrelated-cycle payment",
        "amount_cents": -10000,
    })
    assert payment.status_code == 201, payment.text

    report = client.get(
        "/api/cash-flow/weekly",
        params={"start_date": start.isoformat(), "end_date": "2026-10-31"},
    ).json()
    debts = {item["name"]: item for item in report["minimum_debt_payments"]}

    assert debts["Linked card"]["amount_cents"] == 10000
    assert debts["Linked card"]["qualifying_payments_cents"] == 0
    assert debts["Other card"]["amount_cents"] == 8000
    assert debts["Other card"]["qualifying_payments_cents"] == 0



def test_user_category_named_debt_payment_can_cover_minimum(tmp_path, monkeypatch):
    client = setup_client(tmp_path, monkeypatch)
    start = date(2026, 10, 19)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 100000, buffer_target_cents = 0 WHERE id = 1"
        )
        debt_id = db.execute(
            """INSERT INTO credit_cards
               (name, balance_cents, minimum_payment_cents, due_day_of_month,
                apr_percent, active, next_due_date)
               VALUES ('User-category card', 50000, 5000, 21, 19.9, 1, ?)""",
            ((start + timedelta(days=2)).isoformat(),),
        ).lastrowid
        db.execute(
            """INSERT INTO transactions
               (transaction_date, category, transaction_type, linked_obligation_type,
                linked_obligation_id, description, amount_cents, notes)
               VALUES (?, 'debt payment', 'expense', 'debt', ?, 'Card minimum payment', -5000,
                       'Manually recorded minimum payment')""",
            ((start + timedelta(days=2)).isoformat(), debt_id),
        )

    report = client.get(
        "/api/cash-flow/weekly",
        params={"start_date": start.isoformat(), "end_date": (start + timedelta(days=6)).isoformat()},
    ).json()
    debt = next(item for item in report["minimum_debt_payments"] if item["id"] == debt_id)

    assert debt["qualifying_payments_cents"] == 5000
    assert debt["amount_cents"] == 0
    assert debt["status"] == "recorded"

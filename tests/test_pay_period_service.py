"""Integration tests for the saved-record rolling pay-period forecast."""
from datetime import date

from fastapi.testclient import TestClient

from app import database
from app.main import app


def _client():
    database.initialize_database()
    return TestClient(app)


def test_pay_period_api_uses_snapshot_and_projects_saved_records_once():
    client = _client()
    as_of = date(2026, 11, 8)
    with database.connect() as db:
        db.execute(
            """UPDATE household_settings
               SET starting_cash_cents = 100000, end_of_period_target_cents = 20000,
                   paycheck_group_window_days = 2 WHERE id = 1"""
        )
        db.execute(
            """INSERT INTO cash_balance_snapshots
               (effective_week_start, balance_cents) VALUES ('2026-10-30', 100000)"""
        )
        person_id = db.execute(
            "INSERT INTO persons (name) VALUES ('Household')"
        ).lastrowid
        db.execute(
            """INSERT INTO income_sources
               (person_id, name, net_amount_cents, frequency, next_payday, active)
               VALUES (?, 'Paycheck', 50000, 'biweekly', '2026-11-13', 1)""",
            (person_id,),
        )
        db.execute(
            """INSERT INTO bills
               (name, amount_cents, due_day_of_month, next_due_date,
                flexible_window_days, category, frequency, autopay, active)
               VALUES ('Rent', 30000, 10, '2026-11-10', 0, 'housing', 'monthly', 0, 1)"""
        )
        db.execute(
            """INSERT INTO savings_goals
               (name, target_amount_cents, current_balance_cents, target_date,
                contribution_per_period_cents, active)
               VALUES ('General savings', 0, 0, NULL, 5000, 1)"""
        )
        db.execute(
            """INSERT INTO credit_cards
               (name, balance_cents, minimum_payment_cents, planned_payment_cents,
                due_day_of_month, apr_percent, next_due_date, interest_date, active)
               VALUES ('Card', 100000, 5000, 7000, 20, 12.0,
                       '2026-11-20', '2026-11-15', 1)"""
        )
        db.execute(
            """INSERT INTO transactions
               (transaction_date, category, transaction_type, description, amount_cents)
               VALUES ('2026-11-04', 'groceries', 'expense', 'Groceries', -5000)"""
        )

    response = client.get(
        "/api/cash-flow/pay-periods", params={"as_of_date": as_of.isoformat()}
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["generated_on"] == "2026-11-08"
    assert report["current_cash_cents"] == 95000
    assert report["end_of_period_target_cents"] == 20000

    current = next(p for p in report["periods"] if p["is_current_period"])
    assert current["start_date"] == "2026-10-30"
    assert current["end_date"] == "2026-11-12"
    assert current["income_cents"] == 0
    assert current["bills_cents"] == 30000
    assert current["savings_cents"] == 5000
    assert current["debt_payments_cents"] == 0
    assert current["projected_ending_cash_cents"] == 60000

    following = next(p for p in report["periods"] if p["start_date"] == "2026-11-13")
    assert following["income_cents"] == 50000
    assert following["bills_cents"] == 0
    assert following["savings_cents"] == 5000
    assert following["debt_payments_cents"] == 7000
    assert following["estimated_interest_cents"] == 1000
    assert following["projected_ending_cash_cents"] == 98000
    assert report["projected_debts"][0]["projected_balance_cents"] == 81819


def test_paychecks_within_group_window_both_belong_to_group_last_payday_period():
    client = _client()
    with database.connect() as db:
        db.execute("UPDATE household_settings SET paycheck_group_window_days = 2 WHERE id = 1")
        db.execute(
            """INSERT INTO cash_balance_snapshots
               (effective_week_start, balance_cents) VALUES ('2026-11-01', 50000)"""
        )
        person_id = db.execute(
            "INSERT INTO persons (name) VALUES ('Household')"
        ).lastrowid
        for name, payday in (("Friday pay", "2026-11-13"), ("Sunday pay", "2026-11-15")):
            db.execute(
                """INSERT INTO income_sources
                   (person_id, name, net_amount_cents, frequency, next_payday, active)
                   VALUES (?, ?, 10000, 'weekly', ?, 1)""",
                (person_id, name, payday),
            )

    response = client.get("/api/cash-flow/pay-periods", params={"as_of_date": "2026-11-08"})
    assert response.status_code == 200, response.text
    report = response.json()
    grouped = next(p for p in report["periods"] if p["start_date"] == "2026-11-15")
    assert grouped["paycheck_dates"] == ["2026-11-13", "2026-11-15"]
    assert grouped["income_cents"] == 20000
    assert {item["name"] for item in grouped["income_items"]} == {"Friday pay", "Sunday pay"}


def test_future_recorded_bill_payment_replaces_scheduled_bill_without_double_counting():
    client = _client()
    with database.connect() as db:
        person_id = db.execute(
            "INSERT INTO persons (name) VALUES ('Household')"
        ).lastrowid
        db.execute(
            """INSERT INTO income_sources
               (person_id, name, net_amount_cents, frequency, next_payday, active)
               VALUES (?, 'Paycheck', 100000, 'weekly', '2026-11-13', 1)""",
            (person_id,),
        )
        bill_id = db.execute(
            """INSERT INTO bills
               (name, amount_cents, due_day_of_month, next_due_date,
                flexible_window_days, category, frequency, autopay, active)
               VALUES ('Electric', 12000, 10, '2026-12-10', 2,
                       'utilities', 'monthly', 0, 1)"""
        ).lastrowid
        db.execute(
            """INSERT INTO transactions
               (transaction_date, category, transaction_type, linked_obligation_type,
                linked_obligation_id, description, amount_cents)
               VALUES ('2026-12-09', 'utilities', 'expense', 'bill', ?,
                       'Electric paid early', -12000)""",
            (bill_id,),
        )

    response = client.get("/api/cash-flow/pay-periods", params={"as_of_date": "2026-11-08"})
    assert response.status_code == 200, response.text
    report = response.json()
    period = next(p for p in report["periods"] if p["start_date"] <= "2026-12-09" <= p["end_date"])
    assert not any(item["bill_id"] == bill_id and item["due_date"] == "2026-12-10" for item in period["bills"])
    assert period["other_expenses_cents"] >= 12000


def test_pay_period_report_page_is_printable_and_exposes_report_fields():
    client = _client()
    response = client.get("/pay-periods")
    assert response.status_code == 200
    assert "Three-Month Pay-Period Forecast" in response.text
    assert "Print / Save PDF" in response.text
    assert "/api/cash-flow/pay-periods" in response.text
    assert "Carry above target" in response.text
    assert "break-inside:avoid" in response.text

def test_due_paychecks_and_bills_are_posted_to_internal_ledger_once():
    from app.pay_period_service import post_due_scheduled_transactions

    client = _client()
    with database.connect() as db:
        person_id = db.execute(
            "INSERT INTO persons (name) VALUES ('Household')"
        ).lastrowid
        db.execute(
            """INSERT INTO income_sources
               (person_id, name, net_amount_cents, frequency, next_payday, active)
               VALUES (?, 'Paycheck', 50000, 'weekly', '2026-11-06', 1)""",
            (person_id,),
        )
        db.execute(
            """INSERT INTO bills
               (name, amount_cents, due_day_of_month, next_due_date,
                flexible_window_days, category, frequency, autopay, active)
               VALUES ('Electric', 12000, 8, '2026-11-08', 0,
                       'utilities', 'monthly', 0, 1)"""
        )
        first = post_due_scheduled_transactions(db, date(2026, 11, 8))
        second = post_due_scheduled_transactions(db, date(2026, 11, 8))
        rows = db.execute(
            """SELECT transaction_type, linked_obligation_type, description,
                      amount_cents, notes
               FROM transactions ORDER BY id"""
        ).fetchall()

    assert first["posted_paychecks"] == 1
    assert first["posted_bills"] == 1
    assert second["posted_paychecks"] == 0
    assert second["posted_bills"] == 0
    assert len(rows) == 2
    assert {row["amount_cents"] for row in rows} == {50000, -12000}
    assert all("Automatically posted to the internal ledger" in row["notes"] for row in rows)
    assert client.get("/health").status_code == 200

def test_early_linked_bill_payment_releases_reservation_without_double_subtracting():
    client = _client()
    with database.connect() as db:
        db.execute(
            """INSERT INTO cash_balance_snapshots
               (effective_week_start, balance_cents) VALUES ('2026-11-01', 100000)"""
        )
        bill_id = db.execute(
            """INSERT INTO bills
               (name, amount_cents, due_day_of_month, next_due_date,
                flexible_window_days, category, frequency, autopay, active)
               VALUES ('Water', 12000, 15, '2026-11-15', 7,
                       'utilities', 'monthly', 0, 1)"""
        ).lastrowid
        db.execute(
            """INSERT INTO transactions
               (transaction_date, category, transaction_type, linked_obligation_type,
                linked_obligation_id, description, amount_cents)
               VALUES ('2026-11-10', 'utilities', 'expense', 'bill', ?,
                       'Water paid early', -12000)""",
            (bill_id,),
        )

    response = client.get("/api/cash-flow/pay-periods", params={"as_of_date": "2026-11-12"})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["current_cash_cents"] == 88000
    assert not any(
        item["bill_id"] == bill_id and item["due_date"] == "2026-11-15"
        for period in report["periods"] for item in period["bills"]
    )

def test_unlinked_recorded_paycheck_replaces_estimate_and_keeps_group_period_assignment():
    client = _client()
    with database.connect() as db:
        person_id = db.execute(
            "INSERT INTO persons (name) VALUES ('Household')"
        ).lastrowid
        for name, payday in (("Friday pay", "2026-11-13"), ("Sunday pay", "2026-11-15")):
            db.execute(
                """INSERT INTO income_sources
                   (person_id, name, net_amount_cents, frequency, next_payday, active)
                   VALUES (?, ?, 10000, 'weekly', ?, 1)""",
                (person_id, name, payday),
            )
        db.execute(
            """INSERT INTO transactions
               (transaction_date, category, transaction_type, description, amount_cents)
               VALUES ('2026-11-13', 'paychecks', 'income', 'Friday paycheck received', 10000)"""
        )

    response = client.get("/api/cash-flow/pay-periods", params={"as_of_date": "2026-11-08"})
    assert response.status_code == 200, response.text
    report = response.json()
    grouped = next(p for p in report["periods"] if p["start_date"] == "2026-11-15")
    assert grouped["income_cents"] == 20000
    assert [item["name"] for item in grouped["income_items"]] == ["Sunday pay"]
    assert [item["description"] for item in grouped["transactions"]] == ["Friday paycheck received"]

def test_actual_savings_contribution_satisfies_current_period_only():
    client = _client()
    with database.connect() as db:
        person_id = db.execute(
            "INSERT INTO persons (name) VALUES ('Household')"
        ).lastrowid
        db.execute(
            """INSERT INTO income_sources
               (person_id, name, net_amount_cents, frequency, next_payday, active)
               VALUES (?, 'Paycheck', 50000, 'weekly', '2026-11-13', 1)""",
            (person_id,),
        )
        db.execute(
            """INSERT INTO savings_goals
               (name, target_amount_cents, current_balance_cents, target_date,
                contribution_per_period_cents, last_contribution_date, active)
               VALUES ('General savings', 0, 2500, NULL, 5000, '2026-11-08', 1)"""
        )

    response = client.get("/api/cash-flow/pay-periods", params={"as_of_date": "2026-11-08"})
    assert response.status_code == 200, response.text
    report = response.json()
    current = next(p for p in report["periods"] if p["is_current_period"])
    following = next(p for p in report["periods"] if p["start_date"] == "2026-11-13")
    assert current["savings_cents"] == 0
    assert following["savings_cents"] == 5000

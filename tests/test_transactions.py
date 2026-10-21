"""Integration tests for the manual transaction ledger."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import database
from app.main import app


def client_for(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "transactions.db"))
    database.initialize_database()
    return TestClient(app)


def test_create_list_filter_and_edit_transactions(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    today = date.today()
    income = client.post(
        "/api/transactions",
        json={
            "transaction_date": today.isoformat(),
            "category": " Income ",
            "description": "Paycheck",
            "amount_cents": 150000,
            "account_name": "Checking",
        },
    )
    assert income.status_code == 201, income.text
    assert income.json()["category"] == "income"
    assert income.json()["amount_cents"] == 150000

    expense = client.post(
        "/api/transactions",
        json={
            "transaction_date": (today - timedelta(days=2)).isoformat(),
            "category": "Food",
            "description": "Groceries",
            "amount_cents": -12500,
            "notes": "Weekly shopping",
        },
    )
    assert expense.status_code == 201, expense.text
    assert expense.json()["amount_cents"] == -12500
    assert len(client.get("/api/transactions").json()) == 2
    filtered = client.get(
        "/api/transactions",
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
    )
    assert len(filtered.json()) == 1
    assert filtered.json()[0]["description"] == "Paycheck"

    updated = client.patch(
        f"/api/transactions/{expense.json()['id']}",
        json={"description": "Grocery store", "amount_cents": -13000, "account_name": "Card", "notes": None},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["description"] == "Grocery store"
    assert updated.json()["amount_cents"] == -13000
    assert updated.json()["account_name"] == "Card"
    assert updated.json()["notes"] is None


def test_transaction_validation_and_missing_records(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    today = date.today().isoformat()
    payload = {
        "transaction_date": today,
        "category": "other",
        "description": "Something",
        "amount_cents": 100,
    }
    for invalid in (
        {**payload, "category": " "},
        {**payload, "description": " "},
        {**payload, "transaction_date": "not-a-date"},
    ):
        assert client.post("/api/transactions", json=invalid).status_code == 422
    assert client.get(
        "/api/transactions",
        params={"start_date": "2026-12-31", "end_date": "2026-01-01"},
    ).status_code == 422
    assert client.patch("/api/transactions/9999", json={"description": "Missing"}).status_code == 404
    created = client.post("/api/transactions", json=payload).json()
    assert client.patch(
        f"/api/transactions/{created['id']}", json={"description": None}
    ).status_code == 422


def test_transactions_page_and_dashboard_month_totals(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    today = date.today().isoformat()
    client.post(
        "/api/transactions",
        json={"transaction_date": today, "category": "income", "description": "Pay",
              "amount_cents": 200000},
    )
    client.post(
        "/api/transactions",
        json={"transaction_date": today, "category": "food", "description": "Food",
              "amount_cents": -25000},
    )
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["month_income_cents"] == 200000
    assert dashboard["month_expenses_cents"] == 25000
    assert dashboard["recent_transactions"][0]["description"] in {"Pay", "Food"}
    page = client.get("/transactions")
    assert page.status_code == 200
    assert "Transactions" in page.text
    assert "/api/transactions" in page.text


def test_category_management_preserves_and_reassigns_transaction_history(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    today = date.today().isoformat()
    created_category = client.post(
        "/api/categories", json={"name": "Pet Care", "transaction_type": "expense"}
    )
    assert created_category.status_code == 201, created_category.text
    category = created_category.json()
    transaction = client.post(
        "/api/transactions",
        json={
            "transaction_date": today,
            "transaction_type": "expense",
            "category": "pet care",
            "description": "Dog food",
            "amount_cents": -4200,
        },
    )
    assert transaction.status_code == 201, transaction.text

    renamed = client.patch(
        f"/api/categories/{category['id']}", json={"name": "Pet Supplies"}
    )
    assert renamed.status_code == 200, renamed.text
    listed = client.get("/api/transactions").json()
    assert listed[0]["category"] == "pet supplies"

    deleted = client.request("DELETE",
        f"/api/categories/{category['id']}",
        json={"replacement_category_id": next(
            row["id"] for row in client.get("/api/categories?transaction_type=expense").json()
            if row["name"] == "other"
        )},
    )
    assert deleted.status_code == 200, deleted.text
    listed = client.get("/api/transactions").json()
    assert listed[0]["category"] == "other"
    assert listed[0]["description"] == "Dog food"
    assert client.request("DELETE",
        f"/api/categories/{deleted.json()['id']}",
        json={"replacement_category_id": 1},
    ).status_code == 404


def test_category_types_are_validated_and_transfers_do_not_count_as_income_or_expense(
    tmp_path, monkeypatch
):
    client = client_for(tmp_path, monkeypatch)
    today = date.today().isoformat()
    invalid = client.post(
        "/api/transactions",
        json={
            "transaction_date": today,
            "transaction_type": "income",
            "category": "groceries",
            "description": "Wrong category type",
            "amount_cents": 5000,
        },
    )
    assert invalid.status_code == 422

    for payload in (
        {"transaction_type": "income", "category": "paychecks", "amount_cents": 120000},
        {"transaction_type": "expense", "category": "groceries", "amount_cents": -2500},
        {"transaction_type": "transfer", "category": "account transfer", "amount_cents": 5000},
    ):
        response = client.post(
            "/api/transactions",
            json={
                "transaction_date": today,
                "description": payload["transaction_type"],
                **payload,
            },
        )
        assert response.status_code == 201, response.text

    dashboard = client.get("/api/dashboard").json()
    assert dashboard["month_income_cents"] == 120000
    assert dashboard["month_expenses_cents"] == 2500


def test_category_deletion_requires_same_type_replacement_and_keeps_other(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    category = client.post(
        "/api/categories", json={"name": "One-off", "transaction_type": "expense"}
    ).json()
    income_category = next(
        row for row in client.get("/api/categories?transaction_type=income").json()
        if row["name"] == "paychecks"
    )
    assert client.request("DELETE",
        f"/api/categories/{category['id']}",
        json={"replacement_category_id": income_category["id"]},
    ).status_code == 422
    other = next(
        row for row in client.get("/api/categories?transaction_type=expense").json()
        if row["name"] == "other"
    )
    assert client.request("DELETE",
        f"/api/categories/{other['id']}",
        json={"replacement_category_id": category["id"]},
    ).status_code == 422



def test_debt_payment_suggestions_need_confirmation_and_dismissed_matches_stay_hidden(
    tmp_path, monkeypatch
):
    client = client_for(tmp_path, monkeypatch)
    today = date.today()
    with database.connect() as db:
        first_debt_id = db.execute(
            """INSERT INTO credit_cards
               (name, balance_cents, minimum_payment_cents, due_day_of_month, apr_percent, active, next_due_date)
               VALUES ('Visa Card', 50000, 5000, ?, 19.9, 1, ?)""",
            (today.day, today.isoformat()),
        ).lastrowid
        second_debt_id = db.execute(
            """INSERT INTO credit_cards
               (name, balance_cents, minimum_payment_cents, due_day_of_month, apr_percent, active, next_due_date)
               VALUES ('Auto Loan', 90000, 12000, ?, 8.9, 1, ?)""",
            (today.day, today.isoformat()),
        ).lastrowid

    transaction = client.post(
        "/api/transactions",
        json={
            "transaction_date": today.isoformat(),
            "transaction_type": "expense",
            "category": "debt",
            "description": "Debt payment",
            "amount_cents": -5000,
        },
    )
    assert transaction.status_code == 201, transaction.text
    transaction_id = transaction.json()["id"]

    suggestions = client.get("/api/debt-payment-suggestions")
    assert suggestions.status_code == 200, suggestions.text
    candidates = suggestions.json()
    assert {item["debt_id"] for item in candidates} == {first_debt_id, second_debt_id}
    assert all(item["confidence"] == "review" for item in candidates)
    assert all("Confirm only" in item["reason"] for item in candidates)
    with database.connect() as db:
        row = db.execute(
            "SELECT linked_obligation_type, linked_obligation_id FROM transactions WHERE id = ?",
            (transaction_id,),
        ).fetchone()
    assert row["linked_obligation_type"] is None
    assert row["linked_obligation_id"] is None

    dismissed = next(item for item in candidates if item["debt_id"] == first_debt_id)
    assert client.post(
        f"/api/debt-payment-suggestions/{dismissed['id']}/dismiss"
    ).json()["status"] == "dismissed"
    remaining = client.get("/api/debt-payment-suggestions").json()
    assert [item["debt_id"] for item in remaining] == [second_debt_id]

    chosen = remaining[0]
    confirmed = client.post(f"/api/debt-payment-suggestions/{chosen['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    with database.connect() as db:
        row = db.execute(
            "SELECT linked_obligation_type, linked_obligation_id FROM transactions WHERE id = ?",
            (transaction_id,),
        ).fetchone()
        statuses = db.execute(
            "SELECT status FROM debt_payment_match_suggestions WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchall()
    assert row["linked_obligation_type"] == "debt"
    assert row["linked_obligation_id"] == second_debt_id
    assert {item["status"] for item in statuses} == {"dismissed", "confirmed"}
    assert client.get("/api/debt-payment-suggestions").json() == []


def test_dismissed_debt_match_can_reappear_after_transaction_data_changes(
    tmp_path, monkeypatch
):
    client = client_for(tmp_path, monkeypatch)
    today = date.today()
    with database.connect() as db:
        debt_id = db.execute(
            """INSERT INTO credit_cards
               (name, balance_cents, minimum_payment_cents, due_day_of_month, apr_percent, active, next_due_date)
               VALUES ('Store Card', 30000, 4000, ?, 24.9, 1, ?)""",
            (today.day, today.isoformat()),
        ).lastrowid
    transaction = client.post(
        "/api/transactions",
        json={
            "transaction_date": today.isoformat(),
            "transaction_type": "expense",
            "category": "debt",
            "description": "Debt payment",
            "amount_cents": -4000,
        },
    ).json()
    suggestion = next(
        item for item in client.get("/api/debt-payment-suggestions").json()
        if item["debt_id"] == debt_id
    )
    assert client.post(
        f"/api/debt-payment-suggestions/{suggestion['id']}/dismiss"
    ).status_code == 200
    assert client.get("/api/debt-payment-suggestions").json() == []

    changed = client.patch(
        f"/api/transactions/{transaction['id']}",
        json={"description": "Store Card payment"},
    )
    assert changed.status_code == 200, changed.text
    refreshed = client.get("/api/debt-payment-suggestions").json()
    assert len(refreshed) == 1
    assert refreshed[0]["debt_id"] == debt_id
    assert refreshed[0]["confidence"] == "high"


def test_bill_payment_suggestions_explain_candidates_and_require_confirmation(
    tmp_path, monkeypatch
):
    client = client_for(tmp_path, monkeypatch)
    today = date.today()
    with database.connect() as db:
        bill_id = db.execute(
            """INSERT INTO bills
               (name, amount_cents, due_day_of_month, next_due_date, category, frequency, active)
               VALUES ('Electric', 8000, ?, ?, 'other', 'monthly', 1)""",
            (today.day, today.isoformat()),
        ).lastrowid

    transaction = client.post(
        "/api/transactions",
        json={
            "transaction_date": today.isoformat(),
            "transaction_type": "expense",
            "category": "other",
            "description": "Electric bill payment",
            "amount_cents": -8000,
        },
    )
    assert transaction.status_code == 201, transaction.text
    transaction_id = transaction.json()["id"]

    response = client.get("/api/bill-payment-suggestions")
    assert response.status_code == 200, response.text
    suggestions = response.json()
    suggestion = next(item for item in suggestions if item["bill_id"] == bill_id)
    assert suggestion["transaction_id"] == transaction_id
    assert suggestion["confidence"] == "high"
    assert "include the bill name" in suggestion["reason"]
    assert "$80.00" in suggestion["reason"]
    with database.connect() as db:
        linked = db.execute(
            "SELECT linked_obligation_type, linked_obligation_id FROM transactions WHERE id = ?",
            (transaction_id,),
        ).fetchone()
    assert linked["linked_obligation_type"] is None
    assert linked["linked_obligation_id"] is None

    dismissed = client.post(
        f"/api/bill-payment-suggestions/{suggestion['id']}/dismiss"
    )
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["status"] == "dismissed"
    assert all(item["id"] != suggestion["id"] for item in client.get("/api/bill-payment-suggestions").json())

    changed = client.patch(
        f"/api/transactions/{transaction_id}",
        json={"description": "Electric payment corrected"},
    )
    assert changed.status_code == 200, changed.text
    reconsidered = client.get("/api/bill-payment-suggestions").json()
    assert any(item["id"] == suggestion["id"] for item in reconsidered)


def test_bill_payment_suggestion_confirmation_links_only_selected_bill(
    tmp_path, monkeypatch
):
    client = client_for(tmp_path, monkeypatch)
    today = date.today()
    with database.connect() as db:
        bill_id = db.execute(
            """INSERT INTO bills
               (name, amount_cents, due_day_of_month, next_due_date, category, frequency, active)
               VALUES ('Internet', 6500, ?, ?, 'other', 'monthly', 1)""",
            (today.day, today.isoformat()),
        ).lastrowid
    transaction = client.post(
        "/api/transactions",
        json={
            "transaction_date": today.isoformat(),
            "transaction_type": "expense",
            "category": "other",
            "description": "Internet payment",
            "amount_cents": -6500,
        },
    ).json()
    suggestions = client.get("/api/bill-payment-suggestions").json()
    suggestion = next(item for item in suggestions if item["bill_id"] == bill_id)

    confirmed = client.post(f"/api/bill-payment-suggestions/{suggestion['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    with database.connect() as db:
        linked = db.execute(
            "SELECT linked_obligation_type, linked_obligation_id FROM transactions WHERE id = ?",
            (transaction["id"],),
        ).fetchone()
    assert linked["linked_obligation_type"] == "bill"
    assert linked["linked_obligation_id"] == bill_id
    assert client.get("/api/bill-payment-suggestions").json() == []


def test_bill_payment_suggestions_ignore_unrelated_and_already_covered_expenses(
    tmp_path, monkeypatch
):
    client = client_for(tmp_path, monkeypatch)
    today = date.today()
    with database.connect() as db:
        bill_id = db.execute(
            """INSERT INTO bills
               (name, amount_cents, due_day_of_month, next_due_date, category, frequency, active)
               VALUES ('Water', 7000, ?, ?, 'other', 'monthly', 1)""",
            (today.day, today.isoformat()),
        ).lastrowid
    unrelated = client.post(
        "/api/transactions",
        json={
            "transaction_date": today.isoformat(),
            "transaction_type": "expense",
            "category": "other",
            "description": "Movie tickets",
            "amount_cents": -7000,
        },
    )
    assert unrelated.status_code == 201, unrelated.text
    linked = client.post(
        "/api/transactions",
        json={
            "transaction_date": today.isoformat(),
            "transaction_type": "expense",
            "category": "other",
            "linked_obligation_type": "bill",
            "linked_obligation_id": bill_id,
            "description": "Water bill payment",
            "amount_cents": -7000,
        },
    )
    assert linked.status_code == 201, linked.text
    assert client.get("/api/bill-payment-suggestions").json() == []


def test_weekly_report_includes_possible_unmatched_payment_review(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    page = client.get("/weekly")
    assert page.status_code == 200
    assert "Possible unmatched payments" in page.text
    assert "/api/bill-payment-suggestions" in page.text
    assert "/api/debt-payment-suggestions" in page.text


def test_v20_migration_adds_bill_match_queue_without_changing_transactions(
    tmp_path, monkeypatch
):
    import sqlite3

    client = client_for(tmp_path, monkeypatch)
    today = date.today().isoformat()
    created = client.post(
        "/api/transactions",
        json={
            "transaction_date": today,
            "transaction_type": "expense",
            "category": "other",
            "description": "Water payment",
            "amount_cents": -7000,
        },
    )
    assert created.status_code == 201, created.text
    path = tmp_path / "transactions.db"
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE bill_payment_match_suggestions")
        db.execute("PRAGMA user_version = 20")

    database.initialize_database()
    with database.connect() as db:
        row = db.execute(
            "SELECT description, amount_cents FROM transactions WHERE id = ?",
            (created.json()["id"],),
        ).fetchone()
        assert row["description"] == "Water payment"
        assert row["amount_cents"] == -7000
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'bill_payment_match_suggestions'"
        ).fetchone()[0] == 1
        assert db.execute("PRAGMA user_version").fetchone()[0] == 21


def test_bill_edit_reconsiders_previously_dismissed_payment_suggestion(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch)
    today = date.today()
    bill = client.post(
        "/api/bills",
        json={
            "name": "Phone",
            "amount_cents": 9000,
            "due_day_of_month": today.day,
            "next_due_date": today.isoformat(),
            "frequency": "monthly",
            "category": "other",
            "active": True,
            "autopay": False,
        },
    )
    assert bill.status_code == 201, bill.text
    transaction = client.post(
        "/api/transactions",
        json={
            "transaction_date": today.isoformat(),
            "transaction_type": "expense",
            "category": "other",
            "description": "Phone bill payment",
            "amount_cents": -9000,
        },
    )
    assert transaction.status_code == 201, transaction.text
    suggestion = next(
        item for item in client.get("/api/bill-payment-suggestions").json()
        if item["bill_id"] == bill.json()["id"]
    )
    assert client.post(
        f"/api/bill-payment-suggestions/{suggestion['id']}/dismiss"
    ).status_code == 200
    assert client.get("/api/bill-payment-suggestions").json() == []

    changed = client.patch(
        f"/api/bills/{bill.json()['id']}",
        json={"amount_cents": 9500},
    )
    assert changed.status_code == 200, changed.text
    reconsidered = client.get("/api/bill-payment-suggestions").json()
    refreshed = next(item for item in reconsidered if item["id"] == suggestion["id"])
    assert "$95.00" in refreshed["reason"]

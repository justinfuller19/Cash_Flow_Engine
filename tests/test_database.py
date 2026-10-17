from fastapi.testclient import TestClient

from app import database
from app.main import app


def test_database_initializes_expected_tables(tmp_path, monkeypatch):
    path = tmp_path / "test-cashflow.db"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(path))

    database.initialize_database()

    with database.connect() as db:
        names = {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert {"persons", "income_sources", "bills", "credit_cards",
            "savings_accounts", "savings_goals", "transactions", "categories",
            "payday_plans", "plan_items", "email_notification_log"} <= names


def test_person_and_income_source_endpoints_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "api-test.db"))
    database.initialize_database()
    client = TestClient(app)

    person_response = client.post(
        "/api/people",
        json={"name": "Household member"},
    )
    assert person_response.status_code == 201
    person = person_response.json()
    assert person["name"] == "Household member"
    assert "default_buffer_cents" not in person

    income_response = client.post(
        "/api/income-sources",
        json={
            "person_id": person["id"],
            "name": "Paycheck",
            "net_amount_cents": 140000,
            "frequency": "biweekly",
            "next_payday": "2026-11-13",
        },
    )
    assert income_response.status_code == 201
    assert income_response.json()["net_amount_cents"] == 140000

    assert client.get("/api/people").json()[0]["id"] == person["id"]
    assert client.get("/api/income-sources").json()[0]["person_id"] == person["id"]


def test_income_source_requires_existing_person(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "foreign-key-test.db"))
    database.initialize_database()
    client = TestClient(app)

    response = client.post(
        "/api/income-sources",
        json={
            "person_id": 999,
            "name": "Paycheck",
            "net_amount_cents": 100000,
            "frequency": "weekly",
            "next_payday": "2026-11-13",
        },
    )
    assert response.status_code == 404


def test_household_setup_page_is_available():
    response = TestClient(app).get("/setup")

    assert response.status_code == 200
    assert "Household Setup" in response.text
    assert 'id="person-form"' in response.text
    assert 'id="income-form"' in response.text
    assert "/api/people" in response.text
    assert "/api/income-sources" in response.text


def test_person_rejects_blank_name(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "blank-person.db"))
    database.initialize_database()
    response = TestClient(app).post(
        "/api/people",
        json={"name": "   "},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "name cannot be blank"


def test_income_source_rejects_blank_name(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "blank-income.db"))
    database.initialize_database()
    client = TestClient(app)
    person = client.post("/api/people", json={"name": "Member"}).json()

    response = client.post(
        "/api/income-sources",
        json={
            "person_id": person["id"],
            "name": "   ",
            "net_amount_cents": 100000,
            "frequency": "weekly",
            "next_payday": "2026-11-13",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "name cannot be blank"


def test_income_source_rejects_invalid_frequency_date_and_negative_amount(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "invalid-income.db"))
    database.initialize_database()
    client = TestClient(app)
    person = client.post("/api/people", json={"name": "Member"}).json()
    valid_payload = {
        "person_id": person["id"],
        "name": "Paycheck",
        "net_amount_cents": 100000,
        "frequency": "weekly",
        "next_payday": "2026-11-13",
    }

    invalid_payloads = [
        {**valid_payload, "frequency": "every-other-Friday"},
        {**valid_payload, "next_payday": "not-a-date"},
        {**valid_payload, "net_amount_cents": -1},
    ]
    for payload in invalid_payloads:
        response = client.post("/api/income-sources", json=payload)
        assert response.status_code == 422


def _seed_person_and_income(client):
    person = client.post(
        "/api/people", json={"name": "Member"}
    ).json()
    income = client.post(
        "/api/income-sources",
        json={
            "person_id": person["id"],
            "name": "Paycheck",
            "net_amount_cents": 140000,
            "frequency": "biweekly",
            "next_payday": "2026-11-13",
        },
    ).json()
    return person, income


def test_update_person_edits_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "edit-person.db"))
    database.initialize_database()
    client = TestClient(app)
    person, _ = _seed_person_and_income(client)

    response = client.patch(
        f"/api/people/{person['id']}",
        json={"name": "Renamed"},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Renamed"
    assert updated["active"] is True

    # Unchanged fields are preserved on partial updates.
    response = client.patch(f"/api/people/{person['id']}", json={"active": False})
    assert response.json()["name"] == "Renamed"
    assert response.json()["active"] is False


def test_update_person_deactivate_and_reactivate(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "deactivate-person.db"))
    database.initialize_database()
    client = TestClient(app)
    person, _ = _seed_person_and_income(client)

    response = client.patch(
        f"/api/people/{person['id']}", json={"active": False}
    )
    assert response.status_code == 200
    assert response.json()["active"] is False

    listed = client.get("/api/people").json()
    assert listed[0]["active"] is False

    response = client.patch(f"/api/people/{person['id']}", json={"active": True})
    assert response.json()["active"] is True


def test_update_person_validates_input(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "validate-person.db"))
    database.initialize_database()
    client = TestClient(app)
    person, _ = _seed_person_and_income(client)

    assert client.patch("/api/people/9999", json={"name": "Ghost"}).status_code == 404
    assert (
        client.patch(f"/api/people/{person['id']}", json={"name": "   "}).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/people/{person['id']}", json={"name": "x" * 121}
        ).status_code
        == 422
    )
    assert client.patch(f"/api/people/{person['id']}", json={}).status_code == 422


def test_update_income_source_edits_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "edit-income.db"))
    database.initialize_database()
    client = TestClient(app)
    _, income = _seed_person_and_income(client)

    response = client.patch(
        f"/api/income-sources/{income['id']}",
        json={
            "name": "New job",
            "net_amount_cents": 200000,
            "frequency": "monthly",
            "next_payday": "2026-12-01",
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "New job"
    assert updated["net_amount_cents"] == 200000
    assert updated["frequency"] == "monthly"
    assert updated["next_payday"] == "2026-12-01"


def test_update_income_source_deactivate_and_reassign_person(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "deactivate-income.db"))
    database.initialize_database()
    client = TestClient(app)
    person, income = _seed_person_and_income(client)
    other = client.post("/api/people", json={"name": "Other"}).json()

    response = client.patch(
        f"/api/income-sources/{income['id']}",
        json={"active": False, "person_id": other["id"]},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["active"] is False
    assert updated["person_id"] == other["id"]

    response = client.patch(
        f"/api/income-sources/{income['id']}", json={"active": True}
    )
    assert response.json()["active"] is True


def test_update_income_source_validates_input(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "validate-income.db"))
    database.initialize_database()
    client = TestClient(app)
    _, income = _seed_person_and_income(client)

    assert client.patch("/api/income-sources/9999", json={"name": "Ghost"}).status_code == 404
    assert (
        client.patch(
            f"/api/income-sources/{income['id']}", json={"person_id": 9999}
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/income-sources/{income['id']}", json={"name": "   "}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/income-sources/{income['id']}", json={"net_amount_cents": -5}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/income-sources/{income['id']}", json={"frequency": "sometimes"}
        ).status_code
        == 422
    )
    assert client.patch(f"/api/income-sources/{income['id']}", json={}).status_code == 422


def test_migration_adds_active_to_legacy_persons_table(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "legacy.db"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(path))

    # Simulate a database created before the active flag existed (schema v1).
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            default_buffer_cents INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        "INSERT INTO persons (name, default_buffer_cents) VALUES ('Legacy', 5000)"
    )
    connection.commit()
    connection.close()

    database.initialize_database()

    with database.connect() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(persons)")}
        assert "active" in columns
        row = db.execute("SELECT active FROM persons WHERE name = 'Legacy'").fetchone()
        assert row["active"] == 1
        version = db.execute("PRAGMA user_version").fetchone()[0]
        assert version == database.SCHEMA_VERSION

    # The migrated database serves the new field through the API.
    client = TestClient(app)
    assert client.get("/api/people").json()[0]["active"] is True


def test_schema_migrations_preserve_transactions_and_add_categories(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "legacy-v6.db"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(path))
    # Build a v6-style database by initializing the latest schema, then removing
    # the v7 column/table and marking the database as version 6.
    database.initialize_database()
    with database.connect() as db:
        db.execute("DROP TABLE categories")
        db.execute("ALTER TABLE transactions DROP COLUMN transaction_type")
        db.execute("INSERT INTO transactions "
                   "(transaction_date, category, description, amount_cents) "
                   "VALUES ('2026-10-17', 'income', 'Legacy paycheck', 120000)")
        db.execute("PRAGMA user_version = 6")

    database.initialize_database()
    with database.connect() as db:
        transaction = db.execute(
            "SELECT category, transaction_type FROM transactions WHERE description = 'Legacy paycheck'"
        ).fetchone()
        assert transaction["category"] == "income"
        assert transaction["transaction_type"] == "income"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        assert db.execute(
            "SELECT 1 FROM categories WHERE name = 'paychecks' AND transaction_type = 'income'"
        ).fetchone() is not None


def test_schema_v8_adds_scheduled_item_links(tmp_path, monkeypatch):
    path = tmp_path / "legacy-v7.db"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(path))
    database.initialize_database()
    with database.connect() as db:
        db.execute("DROP INDEX idx_transactions_linked_obligation")
        db.execute("ALTER TABLE transactions DROP COLUMN linked_obligation_type")
        db.execute("ALTER TABLE transactions DROP COLUMN linked_obligation_id")
        db.execute("PRAGMA user_version = 7")

    database.initialize_database()
    with database.connect() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(transactions)")}
        assert {"linked_obligation_type", "linked_obligation_id"} <= columns
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_transactions_linked_obligation'"
        ).fetchone() is not None

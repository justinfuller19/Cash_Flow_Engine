"""Phase 6 schema migration coverage for saved recurring income schedules."""
import sqlite3

from app import database


def test_phase18_income_frequency_migration_preserves_existing_records(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(path))
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE persons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                default_buffer_cents INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE income_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                net_amount_cents INTEGER NOT NULL,
                frequency TEXT NOT NULL CHECK(frequency IN
                    ('weekly', 'biweekly', 'semimonthly', 'monthly', 'irregular')),
                next_payday TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO persons (id, name) VALUES (7, 'Household member');
            INSERT INTO income_sources
                (id, person_id, name, net_amount_cents, frequency, next_payday, active, created_at)
            VALUES (11, 7, 'Weekly paycheck', 125000, 'weekly', '2026-10-23', 1, '2025-01-02 03:04:05');
            PRAGMA user_version = 18;
            """
        )

    database.initialize_database()

    with database.connect() as db:
        row = db.execute("SELECT * FROM income_sources WHERE id = 11").fetchone()
        assert row["person_id"] == 7
        assert row["name"] == "Weekly paycheck"
        assert row["net_amount_cents"] == 125000
        assert row["frequency"] == "weekly"
        assert row["next_payday"] == "2026-10-23"
        assert row["active"] == 1
        assert row["created_at"] == "2025-01-02 03:04:05"
        db.execute(
            """INSERT INTO income_sources
               (person_id, name, net_amount_cents, frequency, next_payday)
               VALUES (7, 'Calendar payday', 80000, 'first_third_friday', '2026-11-06')"""
        )
        assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION

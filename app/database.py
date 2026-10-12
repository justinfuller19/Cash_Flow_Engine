"""SQLite persistence for CashFlow Engine.

The database path can be overridden with CASHFLOW_DB_PATH. The default is
data/cashflow.db relative to the project root. SQLite is used through the
standard library to keep deployment lightweight on Raspberry Pi.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "cashflow.db"

#: Current schema version. Bump when adding a migration to _apply_migrations().
SCHEMA_VERSION = 4


def database_path() -> Path:
    configured = os.environ.get("CASHFLOW_DB_PATH")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DATABASE_PATH


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a configured SQLite connection and commit or roll back atomically."""
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    """Create the current schema and apply any pending migrations."""
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS persons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
                default_buffer_cents INTEGER NOT NULL DEFAULT 0
                    CHECK(default_buffer_cents >= 0),
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS income_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
                name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
                net_amount_cents INTEGER NOT NULL CHECK(net_amount_cents >= 0),
                frequency TEXT NOT NULL CHECK(frequency IN
                    ('weekly', 'biweekly', 'semimonthly', 'monthly', 'irregular')),
                next_payday TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
                amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
                due_day_of_month INTEGER CHECK(due_day_of_month BETWEEN 1 AND 31),
                next_due_date TEXT,
                flexible_window_days INTEGER NOT NULL DEFAULT 0
                    CHECK(flexible_window_days BETWEEN 0 AND 31),
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS credit_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
                balance_cents INTEGER NOT NULL DEFAULT 0 CHECK(balance_cents >= 0),
                minimum_payment_cents INTEGER NOT NULL DEFAULT 0
                    CHECK(minimum_payment_cents >= 0),
                due_day_of_month INTEGER CHECK(due_day_of_month BETWEEN 1 AND 31),
                apr_percent REAL NOT NULL DEFAULT 0 CHECK(apr_percent >= 0),
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS savings_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
                current_balance_cents INTEGER NOT NULL DEFAULT 0
                    CHECK(current_balance_cents >= 0),
                apy_percent REAL NOT NULL DEFAULT 0 CHECK(apy_percent >= 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS savings_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
                target_amount_cents INTEGER NOT NULL CHECK(target_amount_cents >= 0),
                current_balance_cents INTEGER NOT NULL DEFAULT 0
                    CHECK(current_balance_cents >= 0),
                target_date TEXT,
                priority INTEGER NOT NULL DEFAULT 100,
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_date TEXT NOT NULL,
                category TEXT NOT NULL CHECK(length(trim(category)) BETWEEN 1 AND 40),
                description TEXT NOT NULL CHECK(length(trim(description)) BETWEEN 1 AND 200),
                amount_cents INTEGER NOT NULL,
                account_name TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS payday_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_date TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                starting_cash_cents INTEGER NOT NULL CHECK(starting_cash_cents >= 0),
                debt_strategy TEXT NOT NULL CHECK(debt_strategy IN ('avalanche', 'snowball')),
                status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'active', 'completed', 'cancelled')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(period_end >= period_start)
            );

            CREATE TABLE IF NOT EXISTS plan_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL REFERENCES payday_plans(id) ON DELETE CASCADE,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                requested_cents INTEGER NOT NULL CHECK(requested_cents >= 0),
                funded_cents INTEGER NOT NULL CHECK(funded_cents >= 0),
                unfunded_cents INTEGER NOT NULL CHECK(unfunded_cents >= 0),
                due_date TEXT,
                note TEXT,
                is_completed INTEGER NOT NULL DEFAULT 0 CHECK(is_completed IN (0, 1))
            );

            CREATE INDEX IF NOT EXISTS idx_income_person ON income_sources(person_id);
            CREATE INDEX IF NOT EXISTS idx_bills_due_date ON bills(next_due_date);
            CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
            CREATE INDEX IF NOT EXISTS idx_plan_items_plan ON plan_items(plan_id);
            """
        )
        _apply_migrations(db)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _apply_migrations(db: sqlite3.Connection) -> None:
    """Bring an existing database up to SCHEMA_VERSION.

    Fresh databases already match the current schema because the
    CREATE TABLE statements above describe the latest version; migrations
    only adjust databases created by earlier releases.
    """
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version < 2:
        # Version 2 added the active flag to persons (soft deactivation
        # instead of hard deletion for records referenced by history).
        columns = {row["name"] for row in db.execute("PRAGMA table_info(persons)")}
        if "active" not in columns:
            db.execute(
                "ALTER TABLE persons ADD COLUMN active INTEGER NOT NULL "
                "DEFAULT 1 CHECK(active IN (0, 1))"
            )

    if version < 3:
        # Version 3 adds the fields required to manage recurring bills and debts.
        bill_columns = {row["name"] for row in db.execute("PRAGMA table_info(bills)")}
        bill_additions = {
            "category": "TEXT NOT NULL DEFAULT 'other'",
            "frequency": "TEXT NOT NULL DEFAULT 'monthly'",
            "autopay": "INTEGER NOT NULL DEFAULT 0 CHECK(autopay IN (0, 1))",
        }
        for column, declaration in bill_additions.items():
            if column not in bill_columns:
                db.execute(f"ALTER TABLE bills ADD COLUMN {column} {declaration}")

        debt_columns = {row["name"] for row in db.execute("PRAGMA table_info(credit_cards)")}
        if "next_due_date" not in debt_columns:
            db.execute("ALTER TABLE credit_cards ADD COLUMN next_due_date TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS idx_bills_active_due ON bills(active, next_due_date)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_debts_active_due ON credit_cards(active, due_day_of_month)")

    if version < 4:
        # Version 4 supports explicit per-payday savings contributions and saved plan totals.
        goal_columns = {row["name"] for row in db.execute("PRAGMA table_info(savings_goals)")}
        if "contribution_per_period_cents" not in goal_columns:
            db.execute(
                "ALTER TABLE savings_goals ADD COLUMN contribution_per_period_cents "
                "INTEGER NOT NULL DEFAULT 0 CHECK(contribution_per_period_cents >= 0)"
            )

        plan_columns = {row["name"] for row in db.execute("PRAGMA table_info(payday_plans)")}
        plan_additions = {
            "income_cents": "INTEGER NOT NULL DEFAULT 0 CHECK(income_cents >= 0)",
            "total_available_cents": "INTEGER NOT NULL DEFAULT 0 CHECK(total_available_cents >= 0)",
            "allocated_cents": "INTEGER NOT NULL DEFAULT 0 CHECK(allocated_cents >= 0)",
            "unallocated_cents": "INTEGER NOT NULL DEFAULT 0 CHECK(unallocated_cents >= 0)",
            "unfunded_cents": "INTEGER NOT NULL DEFAULT 0 CHECK(unfunded_cents >= 0)",
            "target_card": "TEXT",
        }
        for column, declaration in plan_additions.items():
            if column not in plan_columns:
                db.execute(f"ALTER TABLE payday_plans ADD COLUMN {column} {declaration}")

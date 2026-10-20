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
SCHEMA_VERSION = 10


def base_database_path() -> Path:
    """Return the configured default database path, independent of request tenant."""
    configured = os.environ.get("CASHFLOW_DB_PATH")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DATABASE_PATH


def database_path() -> Path:
    """Resolve the active household's isolated database file.

    Household 1 adopts the existing database to preserve current installations.
    Later households receive sibling files named <database>.household-N.sqlite.
    """
    base = base_database_path()
    try:
        from .auth import current_household_id
        household_id = current_household_id()
    except ImportError:
        household_id = None
    if household_id is None or household_id == 1:
        return base
    return base.with_name(f"{base.stem}.household-{household_id}{base.suffix}")


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

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 40),
                transaction_type TEXT NOT NULL CHECK(transaction_type IN ('income', 'expense', 'transfer')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(transaction_type, name COLLATE NOCASE)
            );

            CREATE TABLE IF NOT EXISTS email_notification_log (
                notification_key TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL,
                last_sent_on TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_date TEXT NOT NULL,
                category TEXT NOT NULL CHECK(length(trim(category)) BETWEEN 1 AND 40),
                transaction_type TEXT NOT NULL DEFAULT 'expense' CHECK(transaction_type IN ('income', 'expense', 'transfer')),
                linked_obligation_type TEXT CHECK(linked_obligation_type IN ('income', 'bill', 'debt')),
                linked_obligation_id INTEGER,
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

            CREATE TABLE IF NOT EXISTS household_settings (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                buffer_target_cents INTEGER NOT NULL DEFAULT 0
                    CHECK(buffer_target_cents >= 0),
                starting_cash_cents INTEGER NOT NULL DEFAULT 0
                    CHECK(starting_cash_cents >= 0),
                financial_week_start TEXT NOT NULL DEFAULT 'Friday'
                    CHECK(financial_week_start IN ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

    if version < 5:
        # Version 5 adds actual-action tracking to payday checklist items.
        item_columns = {row["name"] for row in db.execute("PRAGMA table_info(plan_items)")}
        item_additions = {
            "status": "TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'completed', 'skipped', 'attention'))",
            "actual_amount_cents": "INTEGER CHECK(actual_amount_cents >= 0)",
            "actual_date": "TEXT",
        }
        for column, declaration in item_additions.items():
            if column not in item_columns:
                db.execute(f"ALTER TABLE plan_items ADD COLUMN {column} {declaration}")
        db.execute("UPDATE plan_items SET status = 'completed' WHERE is_completed = 1")
        db.execute("CREATE INDEX IF NOT EXISTS idx_plan_items_status ON plan_items(status, plan_id)")

    if version < 6:
        # Version 6 replaces per-person buffer reservations with one shared
        # household cash buffer target (plus a stored starting cash balance).
        # Existing databases seed the household target from the sum of active
        # members' buffers so reserved amounts do not change on upgrade.
        db.execute(
            """CREATE TABLE IF NOT EXISTS household_settings (
                   id INTEGER PRIMARY KEY CHECK(id = 1),
                   buffer_target_cents INTEGER NOT NULL DEFAULT 0
                       CHECK(buffer_target_cents >= 0),
                   starting_cash_cents INTEGER NOT NULL DEFAULT 0
                       CHECK(starting_cash_cents >= 0),
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        row = db.execute("SELECT id FROM household_settings WHERE id = 1").fetchone()
        if row is None:
            seed_cents = db.execute(
                "SELECT COALESCE(SUM(default_buffer_cents), 0) "
                "FROM persons WHERE active = 1"
            ).fetchone()[0]
            db.execute(
                "INSERT INTO household_settings (id, buffer_target_cents) VALUES (1, ?)",
                (seed_cents,),
            )

    if version < 7:
        # Version 7 adds typed, user-managed transaction categories while preserving
        # the text labels already stored on historical transactions.
        transaction_columns = {row["name"] for row in db.execute("PRAGMA table_info(transactions)")}
        if "transaction_type" not in transaction_columns:
            db.execute(
                "ALTER TABLE transactions ADD COLUMN transaction_type TEXT NOT NULL "
                "DEFAULT 'expense' CHECK(transaction_type IN ('income', 'expense', 'transfer'))"
            )
            # Infer legacy types only when the column is first introduced. If an
            # interrupted/partial migration already created it, do not overwrite
            # transaction types that may have been saved since then.
            db.execute(
                """UPDATE transactions
                   SET transaction_type = CASE
                       WHEN lower(trim(category)) IN ('income', 'paycheck', 'side income',
                           'reimbursements', 'other income') THEN 'income'
                       ELSE 'expense'
                   END"""
            )
        defaults = {
            "expense": ("groceries", "fuel", "restaurants", "household supplies",
                        "shopping", "entertainment", "medical", "housing", "utilities",
                        "transportation", "insurance", "debt", "savings", "food", "other"),
            "income": ("paychecks", "side income", "reimbursements", "other income", "income"),
            "transfer": ("account transfer",),
        }
        for category_type, names in defaults.items():
            for name in names:
                db.execute(
                    "INSERT OR IGNORE INTO categories (name, transaction_type) VALUES (?, ?)",
                    (name, category_type),
                )
        for row in db.execute(
            "SELECT DISTINCT category, transaction_type FROM transactions"
        ).fetchall():
            name = row["category"].strip()[:40] or "other"
            db.execute(
                "INSERT OR IGNORE INTO categories (name, transaction_type) VALUES (?, ?)",
                (name, row["transaction_type"]),
            )
        db.execute("CREATE INDEX IF NOT EXISTS idx_categories_type ON categories(transaction_type, name)")

    if version < 8:
        # Version 8 allows a recorded transaction to be linked explicitly to a
        # scheduled income source, bill, or debt payment so reports can avoid
        # counting the same cash movement twice.
        transaction_columns = {row["name"] for row in db.execute("PRAGMA table_info(transactions)")}
        if "linked_obligation_type" not in transaction_columns:
            db.execute(
                "ALTER TABLE transactions ADD COLUMN linked_obligation_type "
                "TEXT CHECK(linked_obligation_type IN ('income', 'bill', 'debt'))"
            )
        if "linked_obligation_id" not in transaction_columns:
            db.execute("ALTER TABLE transactions ADD COLUMN linked_obligation_id INTEGER")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_linked_obligation "
            "ON transactions(linked_obligation_type, linked_obligation_id)"
        )

    if version < 9:
        # Idempotency log prevents repeated due-date reminders and controls
        # repeated shortfall alerts across daily systemd timer runs.
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_email_notification_last_sent "
            "ON email_notification_log(last_sent_on)"
        )

    if version < 10:
        # Version 10 adds the configurable start day for the household's
        # seven-day financial week. Existing households default to Friday.
        settings_columns = {row["name"] for row in db.execute("PRAGMA table_info(household_settings)")}
        if "financial_week_start" not in settings_columns:
            db.execute(
                "ALTER TABLE household_settings ADD COLUMN financial_week_start TEXT NOT NULL "
                "DEFAULT 'Friday' CHECK(financial_week_start IN "
                "('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'))"
            )

"""Smoke tests for the SQLite backup and restore scripts."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app import database


ROOT = Path(__file__).resolve().parents[1]


def test_backup_and_restore_round_trip(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(db_path))
    monkeypatch.setenv("CASHFLOW_BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("CASHFLOW_BACKUP_RETENTION_DAYS", "30")
    database.initialize_database()
    with database.connect() as db:
        db.execute(
            """INSERT INTO transactions
               (transaction_date, category, description, amount_cents)
               VALUES ('2026-10-12', 'income', 'Original', 50000)"""
        )

    env = os.environ.copy()
    backup = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "backup_database.py")],
        env=env, capture_output=True, text=True, check=True,
    ).stdout.strip()
    backup_path = Path(backup)
    assert backup_path.is_file()

    with database.connect() as db:
        db.execute(
            """INSERT INTO transactions
               (transaction_date, category, description, amount_cents)
               VALUES ('2026-10-12', 'food', 'Added later', -1200)"""
        )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "restore_database.py"), str(backup_path), "--yes"],
        env=env, capture_output=True, text=True, check=True,
    )

    with database.connect() as db:
        rows = db.execute("SELECT description, amount_cents FROM transactions ORDER BY id").fetchall()
        assert [(row["description"], row["amount_cents"]) for row in rows] == [("Original", 50000)]
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert list(backup_dir.glob("cashflow-*.db"))


def test_restore_rejects_non_database_file(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(db_path))
    monkeypatch.setenv("CASHFLOW_BACKUP_DIR", str(tmp_path / "backups"))
    database.initialize_database()
    invalid = tmp_path / "not-a-db.db"
    invalid.write_text("not sqlite", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "restore_database.py"), str(invalid), "--yes"],
        env=os.environ.copy(), capture_output=True, text=True,
    )
    assert result.returncode == 1
    with database.connect() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_server_backup_includes_every_household_and_auth_registry(tmp_path, monkeypatch):
    from app import auth
    from scripts.backup_database import create_server_backups

    base = tmp_path / "cashflow.db"
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(base))
    monkeypatch.setenv("CASHFLOW_BACKUP_DIR", str(backup_dir))
    auth.initialize_auth_database()
    first_id = auth.create_household("First", "first@example.test", "first-household-password")
    second_id = auth.create_household("Second", "second@example.test", "second-household-password")

    for household_id, label in ((first_id, "First private data"), (second_id, "Second private data")):
        token = auth.set_current_household(household_id)
        try:
            database.initialize_database()
            with database.connect() as db:
                db.execute(
                    "INSERT INTO persons (name) VALUES (?)",
                    (label,),
                )
        finally:
            auth.reset_current_household(token)

    backups = create_server_backups()
    assert len(backups) == 3
    assert any("household-1" in path.name for path in backups)
    assert any("household-2" in path.name for path in backups)
    assert any("auth-registry" in path.name for path in backups)

    backed_up_people = {}
    for path in backups:
        with sqlite3.connect(path) as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "persons" in tables:
                rows = db.execute("SELECT name FROM persons").fetchall()
                if rows:
                    backed_up_people[path.name] = [row[0] for row in rows]
            if "households" in tables:
                assert db.execute("SELECT COUNT(*) FROM households").fetchone()[0] == 2
    assert any(names == ["First private data"] for names in backed_up_people.values())
    assert any(names == ["Second private data"] for names in backed_up_people.values())


def test_server_backup_preserves_legacy_database_before_first_account(tmp_path, monkeypatch):
    from app import auth
    from scripts.backup_database import create_server_backups

    base = tmp_path / "legacy.db"
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(base))
    monkeypatch.setenv("CASHFLOW_BACKUP_DIR", str(backup_dir))
    database.initialize_database()
    with database.connect() as db:
        db.execute(
            "INSERT INTO transactions (transaction_date, category, description, amount_cents) "
            "VALUES ('2026-10-17', 'income', 'Legacy record', 10000)"
        )
    auth.initialize_auth_database()  # Empty registry, before the first household registers.

    backups = create_server_backups()
    legacy_backup = next(path for path in backups if "unassigned" in path.name)
    with sqlite3.connect(legacy_backup) as db:
        assert db.execute("SELECT description FROM transactions").fetchone()[0] == "Legacy record"
    assert any("auth-registry" in path.name for path in backups)

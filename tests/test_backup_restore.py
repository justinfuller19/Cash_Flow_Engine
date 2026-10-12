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

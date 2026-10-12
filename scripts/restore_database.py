#!/usr/bin/env python3
"""Restore a SQLite backup after validating it and creating a safety backup."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from backup_database import configured_backup_dir, configured_database_path, create_backup


REQUIRED_TABLES = {
    "persons", "income_sources", "bills", "credit_cards", "savings_goals",
    "transactions", "payday_plans", "plan_items",
}


def validate_backup(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Backup file does not exist: {path}")
    with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"Backup integrity check failed: {integrity}")
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = REQUIRED_TABLES - tables
        if missing:
            raise sqlite3.DatabaseError(
                "Backup is not a CashFlow Engine database; missing tables: "
                + ", ".join(sorted(missing))
            )


def restore_backup(backup_path: Path, database_path: Path | None = None,
                   backup_dir: Path | None = None) -> Path | None:
    backup_path = backup_path.expanduser().resolve()
    database_path = (database_path or configured_database_path()).resolve()
    backup_dir = (backup_dir or configured_backup_dir()).resolve()
    if backup_path == database_path:
        raise ValueError("The backup file cannot be the active database file")
    validate_backup(backup_path)

    safety_backup = None
    if database_path.is_file():
        safety_backup = create_backup(database_path, backup_dir)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True) as source:
            with sqlite3.connect(database_path) as target:
                source.backup(target)
                integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise sqlite3.DatabaseError(f"Restored database integrity check failed: {integrity}")
    except Exception:
        if safety_backup and safety_backup.is_file():
            with sqlite3.connect(f"file:{safety_backup.as_posix()}?mode=ro", uri=True) as source:
                with sqlite3.connect(database_path) as target:
                    source.backup(target)
        raise
    os.chmod(database_path, 0o600)
    return safety_backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup_file", type=Path, help="Backup database file to restore")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive RESTORE confirmation")
    args = parser.parse_args()
    try:
        validate_backup(args.backup_file.expanduser().resolve())
        if not args.yes:
            answer = input(
                "This replaces the active CashFlow Engine database. Type RESTORE to continue: "
            )
            if answer != "RESTORE":
                print("Restore cancelled.")
                return 2
        safety_backup = restore_backup(args.backup_file)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1
    print(f"Restored from: {args.backup_file.resolve()}")
    if safety_backup:
        print(f"Pre-restore safety backup: {safety_backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

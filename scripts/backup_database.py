#!/usr/bin/env python3
"""Create a consistent SQLite backup using SQLite's online backup API."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def configured_database_path() -> Path:
    configured = os.environ.get("CASHFLOW_DB_PATH")
    return Path(configured).expanduser().resolve() if configured else PROJECT_ROOT / "data" / "cashflow.db"


def configured_backup_dir() -> Path:
    configured = os.environ.get("CASHFLOW_BACKUP_DIR")
    return Path(configured).expanduser().resolve() if configured else PROJECT_ROOT / "backups"


def create_backup(source_path: Path | None = None, backup_dir: Path | None = None) -> Path:
    source_path = (source_path or configured_database_path()).resolve()
    backup_dir = (backup_dir or configured_backup_dir()).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Database file does not exist: {source_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"cashflow-{stamp}.db"
    counter = 1
    while destination.exists():
        destination = backup_dir / f"cashflow-{stamp}-{counter}.db"
        counter += 1

    try:
        with sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True) as source:
            with sqlite3.connect(destination) as target:
                source.backup(target)
                result = target.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    raise sqlite3.DatabaseError(f"Backup integrity check failed: {result}")
        os.chmod(destination, 0o600)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    retention_days = int(os.environ.get("CASHFLOW_BACKUP_RETENTION_DAYS", "30"))
    if retention_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        for old in backup_dir.glob("cashflow-*.db"):
            if old == destination or not old.is_file():
                continue
            modified = datetime.fromtimestamp(old.stat().st_mtime, timezone.utc)
            if modified < cutoff:
                old.unlink(missing_ok=True)
    return destination


def main() -> int:
    try:
        destination = create_backup()
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Backup failed: {exc}", file=__import__("sys").stderr)
        return 1
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

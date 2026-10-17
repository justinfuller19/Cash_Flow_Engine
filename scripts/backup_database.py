#!/usr/bin/env python3
"""Create verified SQLite backups for every household and the account registry."""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def configured_database_path() -> Path:
    configured = os.environ.get("CASHFLOW_DB_PATH")
    return Path(configured).expanduser().resolve() if configured else PROJECT_ROOT / "data" / "cashflow.db"


def configured_backup_dir() -> Path:
    configured = os.environ.get("CASHFLOW_BACKUP_DIR")
    return Path(configured).expanduser().resolve() if configured else PROJECT_ROOT / "backups"


def create_backup(
    source_path: Path | None = None,
    backup_dir: Path | None = None,
    label: str | None = None,
) -> Path:
    source_path = (source_path or configured_database_path()).resolve()
    backup_dir = (backup_dir or configured_backup_dir()).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Database file does not exist: {source_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"cashflow-{label}-" if label else "cashflow-"
    destination = backup_dir / f"{prefix}{stamp}.db"
    counter = 1
    while destination.exists():
        destination = backup_dir / f"{prefix}{stamp}-{counter}.db"
        counter += 1

    try:
        with closing(sqlite3.connect(str(source_path))) as source:
            with closing(sqlite3.connect(destination)) as target:
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


def create_server_backups() -> list[Path]:
    """Back up each registered household DB and the shared auth registry."""
    base = configured_database_path()
    auth_path = base.with_name(base.stem + ".auth.sqlite")
    if not auth_path.is_file():
        # Preserve the pre-accounts single-database backup workflow for fresh installs.
        return [create_backup(base)]

    with closing(sqlite3.connect(str(auth_path))) as auth_db:
        household_ids = [
            int(row[0]) for row in auth_db.execute("SELECT id FROM households ORDER BY id")
        ]

    results: list[Path] = []
    if not household_ids and base.is_file():
        # Existing installations may have financial data before the first account is registered.
        results.append(create_backup(base, label="unassigned"))
    for household_id in household_ids:
        source = base if household_id == 1 else base.with_name(
            f"{base.stem}.household-{household_id}{base.suffix}"
        )
        results.append(create_backup(source, label=f"household-{household_id}"))

    # The registry contains credentials hashes and sessions, so it is protected with
    # the same restrictive permissions as financial backups.
    results.append(create_backup(auth_path, label="auth-registry"))
    return results


def main() -> int:
    try:
        destinations = create_server_backups()
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1
    for destination in destinations:
        print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

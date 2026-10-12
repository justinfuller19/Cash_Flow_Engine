# Raspberry Pi deployment and backups

This guide installs CashFlow Engine as a private systemd service. The service binds to `127.0.0.1` by default so the unauthenticated app is not exposed to the LAN or public internet. Remote/LAN access should wait until authentication and a secure access method are configured.

## 1. Install prerequisites

On Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
```

Use a Python version tested by CI (3.11, 3.12, or 3.13).

## 2. Install the application

```bash
sudo useradd --system --home /opt/cashflow-engine --shell /usr/sbin/nologin cashflow
sudo mkdir -p /opt/cashflow-engine /var/lib/cashflow-engine /var/backups/cashflow-engine
sudo git clone https://github.com/justinfuller19/Cash_Flow_Engine.git /opt/cashflow-engine
sudo python3 -m venv /opt/cashflow-engine/.venv
sudo /opt/cashflow-engine/.venv/bin/python -m pip install --upgrade pip
sudo /opt/cashflow-engine/.venv/bin/python -m pip install -r /opt/cashflow-engine/requirements.txt
sudo chown -R root:root /opt/cashflow-engine
sudo chown -R cashflow:cashflow /var/lib/cashflow-engine /var/backups/cashflow-engine
sudo chmod 750 /var/lib/cashflow-engine /var/backups/cashflow-engine
```

If the repository's latest changes are not yet merged, check out the intended release commit before installing rather than running an arbitrary feature branch.

## 3. Configure persistent paths

Create `/etc/cashflow-engine.env`:

```bash
sudo install -o root -g root -m 600 /dev/null /etc/cashflow-engine.env
sudo nano /etc/cashflow-engine.env
```

Set:

```text
CASHFLOW_DB_PATH=/var/lib/cashflow-engine/cashflow.db
CASHFLOW_BACKUP_DIR=/var/backups/cashflow-engine
CASHFLOW_BACKUP_RETENTION_DAYS=30
```

The environment file contains paths only; do not store real household data or database files in Git.

## 4. Install the systemd service

```bash
sudo cp /opt/cashflow-engine/deploy/cashflow-engine.service /etc/systemd/system/
sudo cp /opt/cashflow-engine/deploy/cashflow-engine-backup.service /etc/systemd/system/
sudo cp /opt/cashflow-engine/deploy/cashflow-engine-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cashflow-engine.service
sudo systemctl enable --now cashflow-engine-backup.timer
```

Check status and logs:

```bash
sudo systemctl status cashflow-engine
sudo journalctl -u cashflow-engine -n 100 --no-pager
curl http://127.0.0.1:8000/health
```

Open `http://127.0.0.1:8000` in a browser running on the Pi itself. Keep the service on localhost until the app has authentication and a secure remote-access plan.

## 5. Create and verify a backup

```bash
sudo systemctl start cashflow-engine-backup.service
sudo systemctl status cashflow-engine-backup.service
sudo ls -lh /var/backups/cashflow-engine
```

The backup script uses SQLite's online backup API, validates the copy with `PRAGMA integrity_check`, restricts file permissions, and removes old `cashflow-*.db` backups based on the configured retention period.

You can also run it manually:

```bash
sudo -u cashflow env CASHFLOW_DB_PATH=/var/lib/cashflow-engine/cashflow.db CASHFLOW_BACKUP_DIR=/var/backups/cashflow-engine CASHFLOW_BACKUP_RETENTION_DAYS=30 /opt/cashflow-engine/.venv/bin/python /opt/cashflow-engine/scripts/backup_database.py
```

## 6. Restore a backup

**Restoring replaces the active database.** Stop the app first, select a known-good backup, and review the safety backup the restore script creates before replacing data.

```bash
sudo systemctl stop cashflow-engine
sudo -u cashflow env CASHFLOW_DB_PATH=/var/lib/cashflow-engine/cashflow.db CASHFLOW_BACKUP_DIR=/var/backups/cashflow-engine /opt/cashflow-engine/.venv/bin/python /opt/cashflow-engine/scripts/restore_database.py /var/backups/cashflow-engine/cashflow-YYYYMMDDTHHMMSSZ.db
sudo systemctl start cashflow-engine
sudo systemctl status cashflow-engine
```

The script validates the backup before asking for the exact word `RESTORE`, creates a pre-restore safety backup when the active database exists, and validates the restored database. Add `--yes` only for a deliberate non-interactive restore.

## 7. Updates and rollback

Back up before updating. Review changes, install dependencies in the existing virtual environment, run tests, and restart the service:

```bash
sudo systemctl stop cashflow-engine
cd /opt/cashflow-engine
sudo git fetch origin
sudo git checkout main
sudo git pull --ff-only origin main
sudo /opt/cashflow-engine/.venv/bin/python -m pip install -r requirements.txt
sudo /opt/cashflow-engine/.venv/bin/python -m pytest -q
sudo systemctl start cashflow-engine
sudo journalctl -u cashflow-engine -n 100 --no-pager
```

For rollback, stop the service, check out the previous known-good commit, reinstall its requirements, and restore a database backup if the newer schema migration is not backward-compatible.

## Security and privacy notes

- Do not port-forward this service or expose it to the public internet.
- The current service binds to localhost and has no authentication. Do not change it to `0.0.0.0` without first adding authentication and a secure remote-access layer.
- Protect backups like the live database; they contain the same financial information.
- Test a restore periodically. A backup that has never been restored is not a verified recovery plan.
- SQLite files and backups should remain outside the Git repository.

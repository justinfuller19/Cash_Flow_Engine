# Raspberry Pi deployment and backups

This guide installs CashFlow Engine as a private systemd service. The application listens only on loopback port 8000 behind nginx. Nginx serves it on standard HTTP port 80 (no port number in the browser URL) and allows only private-network client addresses. The app's own household login remains required for financial pages. Do not port-forward the service or expose it to the public internet.

## 1. Install prerequisites

On Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv nginx avahi-daemon
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
# Allow direct connections from private LAN addresses without a second
# Basic-auth prompt. The app's own household login still protects the app.
CASHFLOW_ALLOW_LAN=1
#
# Optional: instead of LAN mode, set BOTH to require Basic authentication
# for all remote requests. Basic auth does not encrypt traffic.
# CASHFLOW_USERNAME=household
# CASHFLOW_PASSWORD=replace-with-a-long-random-secret
#
# Optional SMTP email reports/reminders. Set the three required values together.
# CASHFLOW_SMTP_HOST=smtp.example.com
# CASHFLOW_SMTP_PORT=587
# CASHFLOW_SMTP_USERNAME=your-smtp-username
# CASHFLOW_SMTP_PASSWORD=your-smtp-password
# CASHFLOW_SMTP_FROM=budget@example.com
# CASHFLOW_EMAIL_TO=you@example.com,partner@example.com
# CASHFLOW_REMINDER_DAYS=3
```

The environment file contains local configuration and may contain credentials; keep it root-owned with mode `600` and never commit it or share its contents.

## 4. Install the systemd service

```bash
sudo cp /opt/cashflow-engine/deploy/cashflow-engine.service /etc/systemd/system/\nsudo install -o root -g root -m 644 /opt/cashflow-engine/deploy/nginx-cashflow-engine.conf /etc/nginx/sites-available/cashflow-engine\nsudo ln -sfn /etc/nginx/sites-available/cashflow-engine /etc/nginx/sites-enabled/cashflow-engine\nsudo rm -f /etc/nginx/sites-enabled/default\nsudo nginx -t\nsudo systemctl enable --now nginx
sudo cp /opt/cashflow-engine/deploy/cashflow-engine-backup.service /etc/systemd/system/
sudo cp /opt/cashflow-engine/deploy/cashflow-engine-backup.timer /etc/systemd/system/
sudo cp /opt/cashflow-engine/deploy/cashflow-engine-email-reminders.service /etc/systemd/system/
sudo cp /opt/cashflow-engine/deploy/cashflow-engine-email-reminders.timer /etc/systemd/system/
sudo cp /opt/cashflow-engine/deploy/cashflow-engine-weekly-report.service /etc/systemd/system/
sudo cp /opt/cashflow-engine/deploy/cashflow-engine-weekly-report.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now avahi-daemon
sudo systemctl enable --now cashflow-engine.service
sudo systemctl enable --now cashflow-engine-backup.timer
sudo systemctl enable --now cashflow-engine-email-reminders.timer
sudo systemctl enable --now cashflow-engine-weekly-report.timer
```

Check status and logs:

```bash
sudo systemctl status cashflow-engine
sudo journalctl -u cashflow-engine -n 100 --no-pager
curl http://127.0.0.1:8000/health\ncurl http://127.0.0.1/health
```

From another device on the same Wi-Fi, open `http://<pi-hostname>.local` (replace `<pi-hostname>` with the result of running `hostname` on the Pi). No `:8000` is needed: nginx listens on port 80 and forwards requests internally to the app on `127.0.0.1:8000`. Avahi advertises the Pi hostname through local mDNS. If `.local` name resolution is not supported by a device or router, use the Pi's DHCP-reserved name or IP address. Nginx permits only private IPv4/IPv6 network ranges and denies other addresses. The app's household login remains required. The app itself is bound to loopback and cannot be reached directly from another device on port 8000.

## 5. Configure optional email reports and reminders

Email is disabled unless SMTP settings and at least one recipient are configured in `/etc/cashflow-engine.env`. The application does not store SMTP credentials in the database. Port 587 uses STARTTLS; port 465 uses implicit TLS. Keep the environment file root-owned with mode `600`.

Set `CASHFLOW_SMTP_HOST`, `CASHFLOW_SMTP_FROM`, and `CASHFLOW_EMAIL_TO`; optionally set `CASHFLOW_SMTP_PORT` (default 587), both `CASHFLOW_SMTP_USERNAME` and `CASHFLOW_SMTP_PASSWORD`, and `CASHFLOW_REMINDER_DAYS` (1–6, default 3).

The daily timer runs at 08:00 local server time and sends due-date reminders and shortfall/buffer alerts when needed. A bill/debt reminder is sent once per scheduled item and due date unless its details change. A shortfall alert is sent when the state changes, with a repeat at most once every seven days while the same warning persists. The weekly timer runs Sunday at 19:00 local server time and emails the coming Monday–Sunday forecast once per week.

Check the configured server timezone before enabling the timers. Test the runner without sending mail:

```bash
sudo -u cashflow env CASHFLOW_DB_PATH=/var/lib/cashflow-engine/cashflow.db CASHFLOW_BACKUP_DIR=/var/backups/cashflow-engine /opt/cashflow-engine/.venv/bin/python /opt/cashflow-engine/scripts/send_email_reports.py --mode daily --dry-run
sudo systemctl status cashflow-engine-email-reminders.timer cashflow-engine-weekly-report.timer
sudo journalctl -u cashflow-engine-email-reminders.service -n 50 --no-pager
```

## 6. Create and verify a backup


```bash
sudo systemctl start cashflow-engine-backup.service
sudo systemctl status cashflow-engine-backup.service
sudo ls -lh /var/backups/cashflow-engine
```

The backup script uses SQLite's online backup API, validates every copy with `PRAGMA integrity_check`, restricts file permissions, and removes old `cashflow-*.db` backups based on the configured retention period. With household accounts enabled, it backs up each registered household database and the shared `.auth.sqlite` account registry. Filenames identify household backups (for example, `cashflow-household-2-...`) and the account registry (`cashflow-auth-registry-...`). A missing registered household database causes the backup job to fail rather than silently claiming a complete backup.

The in-app download/reset safety backup still covers only the active household's financial database. Use the scheduled backup set for server-wide recovery. Keep the registry and all household backups from the same backup run together.

You can also run it manually:

```bash
sudo -u cashflow env CASHFLOW_DB_PATH=/var/lib/cashflow-engine/cashflow.db CASHFLOW_BACKUP_DIR=/var/backups/cashflow-engine CASHFLOW_BACKUP_RETENTION_DAYS=30 /opt/cashflow-engine/.venv/bin/python /opt/cashflow-engine/scripts/backup_database.py
```

## 7. Restore a backup

**Restoring replaces the active database.** Stop the app first, select a known-good backup, and review the safety backup the restore script creates before replacing data.

```bash
sudo systemctl stop cashflow-engine
sudo -u cashflow env CASHFLOW_DB_PATH=/var/lib/cashflow-engine/cashflow.db CASHFLOW_BACKUP_DIR=/var/backups/cashflow-engine /opt/cashflow-engine/.venv/bin/python /opt/cashflow-engine/scripts/restore_database.py /var/backups/cashflow-engine/cashflow-YYYYMMDDTHHMMSSZ.db
sudo systemctl start cashflow-engine
sudo systemctl status cashflow-engine
```

The script validates the backup before asking for the exact word `RESTORE`, creates a pre-restore safety backup when the active database exists, and validates the restored database. Add `--yes` only for a deliberate non-interactive restore. To restore a household database, set `CASHFLOW_DB_PATH` to that household's live database path before running the restore command. To restore the shared account registry, stop the app and carefully restore the matching `cashflow-auth-registry-...` backup to the configured base database's `.auth.sqlite` path; preserve the original file as a safety copy first. Restore the registry and household databases from a consistent backup set.

## 8. Updates and rollback

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
- Nginx listens on port 80 and allows private-network address ranges only; the application listens on loopback port 8000 behind nginx. The household login remains required for financial pages.
- If CASHFLOW_ALLOW_LAN is disabled, non-loopback requests are blocked unless Basic authentication is configured.
- Basic authentication is not encryption. Use HTTPS or a trusted VPN for remote access; do not expose the service directly to the public internet.
- Protect backups like the live database; they contain the same financial information.
- Test a restore periodically. A backup that has never been restored is not a verified recovery plan.
- SQLite files and backups should remain outside the Git repository.


## Updating from the Settings & Data page

Once the application updater is installed, open **Settings & Data → Update application**, type
`UPDATE APPLICATION`, and confirm. It backs up all configured household databases using the
existing backup service, fetches the latest merged `main` branch, installs Python requirements,
runs the automated test suite, synchronizes the systemd service definition, and checks its local
health endpoint. When the existing environment file does not already specify
`CASHFLOW_ALLOW_LAN`, the updater enables trusted-LAN mode without overwriting other settings.
It installs and configures nginx as a private-network-only reverse proxy on port 80, keeps the app
bound to loopback port 8000, validates the nginx configuration, and starts/reloads nginx. It also
attempts to install and start Avahi for hostname discovery; if that is unavailable, use the Pi's
DHCP name or IP address. The page shows the result after the application returns.

The updater is designed for the root-owned `/opt/cashflow-engine` installation created by the
Raspberry Pi installer. The app service account can only create a fixed request marker watched by a root-owned systemd path unit;
it cannot invoke shell commands or pass arbitrary paths or branch names. The Pi's root Git credentials
must still be able to fetch this private repository.

For an installation created before the updater feature was added, first pull the merged code
using the normal terminal update procedure, then install the updater once:

```bash
cd /opt/cashflow-engine
sudo bash scripts/install_application_updater.sh
```

Do not expose the app directly to the public internet. A failed update attempts to restore the
previous application revision and restart the service; always keep verified backups and review
the service logs if the page reports a failure.

**Rollback limitation:** a failed update attempts to restore the previous application code and dependencies, but it does not automatically reverse database schema changes made by a newer version. The pre-update backup is retained; if an update involving a schema change fails, stop using the app and follow the database restore procedure before continuing. Do not treat code rollback alone as proof that the database was rolled back.

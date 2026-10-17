# CashFlow Engine — Raspberry Pi install bundle

This folder is a small, zip-friendly installer bundle for setting up CashFlow Engine on Raspberry Pi OS / Debian. It includes a one-command installer and a helper that packages this folder as a ZIP.

## Quick install

1. Download or copy this `raspberry-pi-package` folder to your Raspberry Pi.
2. Open a terminal and change into the folder.
3. Run:

   ```bash
   sudo bash install.sh
   ```

The installer installs system packages, clones the latest merged `main` branch into `/opt/cashflow-engine`, creates a dedicated unprivileged service account, installs Python dependencies, runs the tests, creates protected persistent-data and backup directories, installs systemd units, and starts the app and scheduled timers.

**Run this after the installer PR has been merged to `main`.** It intentionally installs the merged application rather than copying a potentially incomplete source snapshot from the ZIP.

## First launch

On the Raspberry Pi itself, open:

- App: <http://127.0.0.1:8000>
- Create the first household account: <http://127.0.0.1:8000/register>

The app uses a dedicated system service and starts automatically after reboot.

## Useful commands

```bash
sudo systemctl status cashflow-engine
sudo journalctl -u cashflow-engine -f
sudo systemctl restart cashflow-engine
sudo systemctl status cashflow-engine-backup.timer
sudo ls -lh /var/backups/cashflow-engine
```

## Where data lives

- App code: `/opt/cashflow-engine`
- Persistent database(s): `/var/lib/cashflow-engine`
- Scheduled backups: `/var/backups/cashflow-engine`
- Private configuration: `/etc/cashflow-engine.env` (root-owned, mode 600)

The app binds to `127.0.0.1:8000` by default. It is not directly exposed to your network or the internet. Do not port-forward it. For remote access, use a properly configured HTTPS reverse proxy or trusted VPN and follow `docs/DEPLOY_RASPBERRY_PI.md`.

## Optional settings

The installer creates `/etc/cashflow-engine.env` with persistent database and backup paths. To configure optional Basic authentication or SMTP notifications, edit it:

```bash
sudo nano /etc/cashflow-engine.env
sudo systemctl restart cashflow-engine
sudo systemctl restart cashflow-engine-email-reminders.timer cashflow-engine-weekly-report.timer
```

If you enable email, set the SMTP host and sender, credentials if required, and a recipient (or configure the household recipient in Account settings). Never share or commit this file.

## ZIP the bundle

From the repository root, run:

```bash
bash raspberry-pi-package/build-zip.sh
```

This creates `cashflow-engine-raspberry-pi.zip` in the repository root. The ZIP contains only this installer bundle, not financial data, credentials, a database, or the full application source. The installer retrieves the current merged application from GitHub.

## Safety notes

- The installer stops if `/opt/cashflow-engine` already exists, to avoid overwriting an existing installation.
- It does not create or configure port forwarding.
- Keep backups private; they contain household financial information.
- Review `docs/DEPLOY_RASPBERRY_PI.md` in the installed application for backup restore, updates, and detailed operations.

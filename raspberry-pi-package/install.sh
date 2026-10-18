#!/usr/bin/env bash
# CashFlow Engine Raspberry Pi installer.
# Run with: sudo bash install.sh
set -Eeuo pipefail

readonly APP_NAME="cashflow-engine"
readonly REPO_URL="https://github.com/justinfuller19/Cash_Flow_Engine.git"
readonly INSTALL_DIR="/opt/cashflow-engine"
readonly DATA_DIR="/var/lib/cashflow-engine"
readonly BACKUP_DIR="/var/backups/cashflow-engine"
readonly ENV_FILE="/etc/cashflow-engine.env"
readonly SERVICE_USER="cashflow"
readonly PACKAGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '\n[%s] %s\n' "CashFlow Engine" "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

if [[ "${EUID}" -ne 0 ]]; then
  die "Please run this installer as root: sudo bash install.sh"
fi

if [[ ! -r /etc/os-release ]]; then
  die "Cannot identify this operating system. Use Raspberry Pi OS or Debian."
fi
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in
  raspbian|debian) ;;
  *)
    case " ${ID_LIKE:-} " in
      *" debian "*) ;;
      *) die "This installer supports Raspberry Pi OS / Debian-based systems, not ID='${ID:-unknown}'." ;;
    esac
    ;;
esac

if [[ -e "$INSTALL_DIR" ]]; then
  die "$INSTALL_DIR already exists. To avoid overwriting an existing installation, this installer stops here. Follow docs/DEPLOY_RASPBERRY_PI.md for updates or migration."
fi

log "Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git python3 python3-venv python3-pip ca-certificates curl

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$PYTHON_VERSION" in
  3.11|3.12|3.13) log "Using supported Python $PYTHON_VERSION." ;;
  *) die "Python $PYTHON_VERSION is not in the tested range (3.11–3.13). Install a supported Python version before continuing." ;;
esac

log "Creating the service account and persistent data directories..."
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
install -d -o root -g root -m 755 "$INSTALL_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 750 "$DATA_DIR" "$BACKUP_DIR"

log "Downloading the latest merged main branch..."
git clone --depth 1 --branch main "$REPO_URL" "$INSTALL_DIR"
chown -R root:root "$INSTALL_DIR"

log "Creating the Python environment and installing dependencies..."
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

log "Running the automated test suite..."
(
  cd "$INSTALL_DIR"
  "$INSTALL_DIR/.venv/bin/python" -m pytest -q
)

if [[ ! -e "$ENV_FILE" ]]; then
  log "Creating protected environment configuration at $ENV_FILE..."
  cat > "$ENV_FILE" <<'ENV'
# CashFlow Engine persistent paths. Keep this file private (mode 600).
CASHFLOW_DB_PATH=/var/lib/cashflow-engine/cashflow.db
CASHFLOW_BACKUP_DIR=/var/backups/cashflow-engine
CASHFLOW_BACKUP_RETENTION_DAYS=30

# Optional HTTP Basic authentication. If set, configure BOTH values.
# For remote access, use HTTPS or a trusted VPN; never expose this app directly
# to the public internet. Household login is also required.
# CASHFLOW_USERNAME=household
# CASHFLOW_PASSWORD=replace-with-a-long-random-secret

# Optional email reminders and weekly reports. To enable SMTP, set host, sender,
# and a recipient or configure a recipient under the app's Account settings.
# CASHFLOW_SMTP_HOST=smtp.example.com
# CASHFLOW_SMTP_PORT=587
# CASHFLOW_SMTP_USERNAME=your-smtp-username
# CASHFLOW_SMTP_PASSWORD=your-smtp-password
# CASHFLOW_SMTP_FROM=budget@example.com
# CASHFLOW_EMAIL_TO=you@example.com
# CASHFLOW_REMINDER_DAYS=3
ENV
  chown root:root "$ENV_FILE"
  chmod 600 "$ENV_FILE"
else
  log "Keeping existing $ENV_FILE unchanged."
  chmod 600 "$ENV_FILE"
  chown root:root "$ENV_FILE"
fi

log "Installing systemd service and scheduled-job definitions..."
for unit in \
  cashflow-engine.service \
  cashflow-engine-backup.service \
  cashflow-engine-backup.timer \
  cashflow-engine-email-reminders.service \
  cashflow-engine-email-reminders.timer \
  cashflow-engine-weekly-report.service \
  cashflow-engine-weekly-report.timer; do
  [[ -f "$INSTALL_DIR/deploy/$unit" ]] || die "Required service file is missing: deploy/$unit"
  install -o root -g root -m 644 "$INSTALL_DIR/deploy/$unit" "/etc/systemd/system/$unit"
done

systemctl daemon-reload
systemctl enable --now cashflow-engine.service
systemctl enable --now cashflow-engine-backup.timer
systemctl enable --now cashflow-engine-email-reminders.timer
systemctl enable --now cashflow-engine-weekly-report.timer

log "Waiting briefly for the local health endpoint..."
for attempt in {1..20}; do
  if curl --fail --silent http://127.0.0.1:8000/health >/dev/null; then
    break
  fi
  if [[ "$attempt" -eq 20 ]]; then
    systemctl --no-pager --full status cashflow-engine.service || true
    journalctl -u cashflow-engine.service -n 60 --no-pager || true
    die "The service did not become healthy. Review the logs shown above."
  fi
  sleep 2
done

log "Installation complete."
cat <<'DONE'

CashFlow Engine is running as a systemd service.

  Local app:       http://127.0.0.1:8000
  First-time setup: http://127.0.0.1:8000/register
  Service status:  sudo systemctl status cashflow-engine
  Live logs:       sudo journalctl -u cashflow-engine -f
  Environment:     sudo nano /etc/cashflow-engine.env
  Restart service: sudo systemctl restart cashflow-engine

Open the URL in a browser on the Raspberry Pi itself. The service intentionally
binds to localhost and should not be exposed directly to the internet.
Create your household account during first-time setup. Email is optional.
Backups are scheduled daily. Keep the Raspberry Pi updated and test restores.

DONE

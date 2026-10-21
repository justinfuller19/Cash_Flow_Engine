#!/usr/bin/env bash
# Root-owned updater. This script intentionally accepts no user-controlled arguments.
set -Eeuo pipefail
umask 022

readonly INSTALL_DIR="/opt/cashflow-engine"
readonly STATUS_FILE="/var/lib/cashflow-engine/application-update-status.json"
readonly BACKUP_SERVICE="cashflow-engine-backup.service"
readonly APP_SERVICE="cashflow-engine.service"
readonly SERVICE_UNIT_FILE="/etc/systemd/system/cashflow-engine.service"
readonly ENV_FILE="/etc/cashflow-engine.env"

previous_sha=""
app_stopped=0

# Keep credentials available for the authenticated health check only.
health_username="${CASHFLOW_USERNAME:-}"
health_password="${CASHFLOW_PASSWORD:-}"
unset CASHFLOW_USERNAME CASHFLOW_PASSWORD

write_status() {
  local state="$1"
  local message="$2"
  local timestamp status_dir temp_file
  timestamp="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"
  status_dir="$(dirname "$STATUS_FILE")"
  install -d -o cashflow -g cashflow -m 750 "$status_dir"
  temp_file="$(mktemp "$status_dir/.application-update-status.XXXXXX")"
  python3 - "$state" "$message" "$timestamp" > "$temp_file" <<'PY'
import json
import sys
print(json.dumps({"state": sys.argv[1], "message": sys.argv[2], "updated_at": sys.argv[3]}))
PY
  chown root:cashflow "$temp_file"
  chmod 640 "$temp_file"
  mv -f "$temp_file" "$STATUS_FILE"
}

fail_update() {
  local message="$1"
  trap - ERR
  if [[ -n "$previous_sha" ]]; then
    git -C "$INSTALL_DIR" reset --hard "$previous_sha" >/dev/null 2>&1 || true
    "$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt" >/dev/null 2>&1 || true
    if [[ -f "$INSTALL_DIR/deploy/cashflow-engine.service" ]]; then
      install -o root -g root -m 644 "$INSTALL_DIR/deploy/cashflow-engine.service" "$SERVICE_UNIT_FILE" >/dev/null 2>&1 || true
      systemctl daemon-reload >/dev/null 2>&1 || true
    fi
  fi
  if [[ "$app_stopped" -eq 1 ]]; then
    systemctl start "$APP_SERVICE" >/dev/null 2>&1 || true
  else
    systemctl restart "$APP_SERVICE" >/dev/null 2>&1 || true
  fi
  write_status "failed" "$message The previous application code revision was restored where possible. Check the service logs; database schema changes are not automatically reversed."
  exit 1
}
trap 'fail_update "Update failed near line $LINENO."' ERR

rm -f /var/lib/cashflow-engine/application-update-request
cd "$INSTALL_DIR"
previous_sha="$(git rev-parse HEAD)"
if ! git remote get-url origin | grep -Eq '^https://github\.com/justinfuller19/Cash_Flow_Engine\.git$'; then
  write_status "failed" "Update cancelled because the origin remote is not the expected CashFlow Engine repository."
  trap - ERR
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  write_status "failed" "Update cancelled because the application checkout has local changes. Resolve them before updating."
  trap - ERR
  exit 1
fi

write_status "running" "Creating a safety backup before updating."
systemctl start "$BACKUP_SERVICE"

write_status "running" "Downloading the latest merged main branch."
git fetch --depth 1 origin main

write_status "running" "Installing the update and running automated tests."
systemctl stop "$APP_SERVICE"
app_stopped=1
git reset --hard FETCH_HEAD
"$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"
"$INSTALL_DIR/.venv/bin/python" -m pytest -q

write_status "running" "Applying the service configuration for trusted home-network access."
install -o root -g root -m 644 "$INSTALL_DIR/deploy/cashflow-engine.service" "$SERVICE_UNIT_FILE"
systemctl daemon-reload
# Preserve all existing settings and explicit choices. Add LAN mode only when
# the setting has never been configured, so the household login replaces the
# redundant Basic-auth prompt for private-network clients after this update.
if [[ ! -f "$ENV_FILE" ]]; then
  install -o root -g root -m 600 /dev/null "$ENV_FILE"
fi
if ! grep -Eq '^[[:space:]]*CASHFLOW_ALLOW_LAN=' "$ENV_FILE"; then
  printf '\nCASHFLOW_ALLOW_LAN=1\n' >> "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"
if ! command -v avahi-daemon >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
  apt-get install -y avahi-daemon || true
fi
if command -v avahi-daemon >/dev/null 2>&1; then
  systemctl enable --now avahi-daemon || true
fi

write_status "running" "Starting the updated application."
systemctl start "$APP_SERVICE"
app_stopped=0

health_check() {
  CASHFLOW_USERNAME="$health_username" CASHFLOW_PASSWORD="$health_password" "$INSTALL_DIR/.venv/bin/python" - <<'PY'
import base64
import os
import urllib.error
import urllib.request

username = os.environ.get("CASHFLOW_USERNAME", "")
password = os.environ.get("CASHFLOW_PASSWORD", "")
if bool(username) != bool(password):
    raise SystemExit(1)

headers = {}
if username and password:
    credentials = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    headers["Authorization"] = f"Basic {credentials}"

request = urllib.request.Request("http://127.0.0.1:8000/health", headers=headers)
try:
    with urllib.request.urlopen(request, timeout=2) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except (urllib.error.URLError, TimeoutError, OSError):
    raise SystemExit(1)
PY
}

for attempt in {1..30}; do
  if health_check; then
    new_sha="$(git rev-parse --short HEAD)"
    write_status "completed" "Update completed successfully (revision $new_sha)."
    trap - ERR
    exit 0
  fi
  sleep 2
done

fail_update "The updated application did not pass its health check."

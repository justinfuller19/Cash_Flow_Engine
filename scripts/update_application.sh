#!/usr/bin/env bash
# Root-owned updater. This script intentionally accepts no user-controlled arguments.
set -Eeuo pipefail
umask 022

readonly INSTALL_DIR="/opt/cashflow-engine"
readonly STATUS_FILE="/var/lib/cashflow-engine/application-update-status.json"
readonly BACKUP_SERVICE="cashflow-engine-backup.service"
readonly APP_SERVICE="cashflow-engine.service"

previous_sha=""
app_stopped=0

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

write_status "running" "Starting the updated application."
systemctl start "$APP_SERVICE"
app_stopped=0

for attempt in {1..30}; do
  if curl --fail --silent http://127.0.0.1:8000/health >/dev/null; then
    new_sha="$(git rev-parse --short HEAD)"
    write_status "completed" "Update completed successfully (revision $new_sha)."
    trap - ERR
    exit 0
  fi
  sleep 2
done

fail_update "The updated application did not pass its health check."

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
  local timestamp
  timestamp="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"
  install -d -o cashflow -g cashflow -m 750 "$(dirname "$STATUS_FILE")"
  local temp_file
  temp_file="$(mktemp)"
  printf '{"state":"%s","message":%s,"updated_at":"%s"}\n' \
    "$state" "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$message")" "$timestamp" > "$temp_file"
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
  write_status "failed" "$message The previous application revision was restored where possible. Check the service logs."
  exit 1
}
trap 'fail_update "Update failed near line $LINENO."' ERR

write_status "running" "Creating a safety backup before updating."
cd "$INSTALL_DIR"
previous_sha="$(git rev-parse HEAD)"
git remote get-url origin | grep -Eq '^https://github\.com/justinfuller19/Cash_Flow_Engine\.git$'
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

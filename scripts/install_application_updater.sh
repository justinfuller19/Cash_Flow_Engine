#!/usr/bin/env bash
# Install the narrowly scoped privilege boundary used by the Settings & Data updater.
set -Eeuo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "Run with: sudo bash scripts/install_application_updater.sh" >&2
  exit 1
fi

readonly INSTALL_DIR="/opt/cashflow-engine"
readonly SERVICE_USER="cashflow"
readonly UNIT_SOURCE="$INSTALL_DIR/deploy/cashflow-engine-update.service"
readonly SUDOERS_SOURCE="$INSTALL_DIR/deploy/cashflow-engine-update.sudoers"

[[ -x "$INSTALL_DIR/scripts/update_application.sh" ]] || {
  echo "Updater script is missing or not executable." >&2
  exit 1
}
[[ -f "$UNIT_SOURCE" && -f "$SUDOERS_SOURCE" ]] || {
  echo "Updater systemd or sudoers definition is missing." >&2
  exit 1
}
id "$SERVICE_USER" >/dev/null 2>&1 || {
  echo "The $SERVICE_USER service account does not exist." >&2
  exit 1
}

install -o root -g root -m 644 "$UNIT_SOURCE" /etc/systemd/system/cashflow-engine-update.service
install -o root -g root -m 440 "$SUDOERS_SOURCE" /etc/sudoers.d/cashflow-engine-update
visudo -cf /etc/sudoers.d/cashflow-engine-update
systemctl daemon-reload
echo "Application updater installed. The Settings & Data page can now request updates."

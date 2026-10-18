#!/usr/bin/env bash
# Install the systemd path/service boundary used by the Settings & Data updater.
set -Eeuo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "Run with: sudo bash scripts/install_application_updater.sh" >&2
  exit 1
fi

readonly INSTALL_DIR="/opt/cashflow-engine"
readonly SERVICE_USER="cashflow"
readonly UNIT_SOURCE="$INSTALL_DIR/deploy/cashflow-engine-update.service"
readonly PATH_SOURCE="$INSTALL_DIR/deploy/cashflow-engine-update.path"

[[ -f "$INSTALL_DIR/scripts/update_application.sh" ]] || {
  echo "Updater script is missing." >&2
  exit 1
}
[[ -f "$UNIT_SOURCE" && -f "$PATH_SOURCE" ]] || {
  echo "Updater systemd definitions are missing." >&2
  exit 1
}
id "$SERVICE_USER" >/dev/null 2>&1 || {
  echo "The $SERVICE_USER service account does not exist." >&2
  exit 1
}

# The updater runs as root and executes code from this checkout. Keep the entire
# checkout (including .git metadata and requirements) unwritable by the app user.
chown -R root:root "$INSTALL_DIR"
chmod 755 "$INSTALL_DIR/scripts/update_application.sh"
install -o root -g root -m 644 "$UNIT_SOURCE" /etc/systemd/system/cashflow-engine-update.service
install -o root -g root -m 644 "$PATH_SOURCE" /etc/systemd/system/cashflow-engine-update.path
systemctl daemon-reload
systemctl enable --now cashflow-engine-update.path
echo "Application updater installed. The Settings & Data page can now request updates."

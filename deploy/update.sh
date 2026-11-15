#!/usr/bin/env bash
# Install a newer REDZONE version on the server (keeps the database, settings and backups).
# Usage, from the unzipped NEW release folder:  sudo bash deploy/update.sh
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/redzone
bash "$APP/deploy/backup.sh"                     # safety copy first
cp -r "$SRC/scripts" "$SRC/webapp" "$SRC/deploy" "$SRC/requirements.txt" "$APP/"
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt"
chown -R redzone:redzone "$APP"
cp "$APP/deploy/redzone.service" /etc/systemd/system/redzone.service
systemctl daemon-reload
systemctl restart redzone
sleep 3
curl -fsS http://127.0.0.1:8000/healthz && echo "  updated and running"

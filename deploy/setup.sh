#!/usr/bin/env bash
# REDZONE - one-time server setup (Ubuntu 22.04 / 24.04, x86 or Arm).
#
# Usage, from the unzipped release folder on the server:
#     sudo bash deploy/setup.sh                      # free address: <your-ip>.sslip.io
#     sudo bash deploy/setup.sh redzone.duckdns.org  # or your own free/paid hostname
#
# What it does:
#   1. installs Python and Caddy (automatic HTTPS certificates)
#   2. opens ports 80 and 443 in the server's own firewall
#   3. copies REDZONE to /opt/redzone and installs its Python packages
#   4. creates the settings file /etc/redzone/redzone.env (keys stay out of the code)
#   5. runs REDZONE as a service that restarts by itself, behind Caddy
#   6. schedules a daily database backup (kept 14 days)
# Safe to run again: it keeps your settings, database and backups.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash deploy/setup.sh"; exit 1; fi
SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/redzone
ENVFILE=/etc/redzone/redzone.env

# ---------- 0. hostname ----------
HOST="${1:-}"
if [[ -z "$HOST" ]]; then
  IP="$(curl -fsS https://api.ipify.org || true)"
  if [[ -z "$IP" ]]; then echo "Could not find this server's public IP. Pass a hostname: sudo bash deploy/setup.sh my.host"; exit 1; fi
  HOST="${IP//./-}.sslip.io"
fi
echo "==> REDZONE will be served at https://$HOST"

# ---------- 1. packages ----------
echo "==> Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip curl gnupg debian-keyring debian-archive-keyring apt-transport-https unzip
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

# ---------- 2. firewall (Oracle Cloud Ubuntu images block everything but SSH) ----------
echo "==> Opening ports 80 and 443 in the server firewall"
for PORT in 80 443; do
  if ! iptables -C INPUT -p tcp --dport "$PORT" -m state --state NEW -j ACCEPT 2>/dev/null; then
    POS="$(iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')"
    if [[ -n "$POS" ]]; then iptables -I INPUT "$POS" -p tcp --dport "$PORT" -m state --state NEW -j ACCEPT
    else iptables -A INPUT -p tcp --dport "$PORT" -m state --state NEW -j ACCEPT; fi
  fi
done
if command -v netfilter-persistent >/dev/null; then netfilter-persistent save; fi
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then ufw allow 80/tcp; ufw allow 443/tcp; fi

# ---------- 3. application ----------
echo "==> Installing REDZONE in $APP"
id redzone >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin redzone
mkdir -p "$APP/data" "$APP/backups"
cp -r "$SRC/scripts" "$SRC/webapp" "$SRC/deploy" "$SRC/requirements.txt" "$APP/"
[[ -d "$SRC/docs" ]] && cp -r "$SRC/docs" "$APP/"
if [[ -f "$SRC/data/redzone.duckdb" && ! -f "$APP/data/redzone.duckdb" ]]; then
  echo "==> Copying the database (first install only)"
  cp "$SRC/data/redzone.duckdb" "$APP/data/"
fi
[[ -f "$SRC/data/secret.key" && ! -f "$APP/data/secret.key" ]] && cp "$SRC/data/secret.key" "$APP/data/"
if [[ ! -f "$APP/data/redzone.duckdb" ]]; then echo "ERROR: no data/redzone.duckdb in the release or in $APP/data"; exit 1; fi
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --upgrade pip >/dev/null
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt"
chown -R redzone:redzone "$APP"

# ---------- 4. settings ----------
mkdir -p /etc/redzone
if [[ ! -f "$ENVFILE" ]]; then
  cp "$APP/deploy/redzone.env.example" "$ENVFILE"
  echo "==> Created $ENVFILE - add your FIRMS key and admin email there"
fi
sed -i "s|^REDZONE_BASE_URL=.*|REDZONE_BASE_URL=https://$HOST|" "$ENVFILE"
chown root:redzone "$ENVFILE"; chmod 640 "$ENVFILE"

# ---------- 5. service + HTTPS ----------
cp "$APP/deploy/redzone.service" /etc/systemd/system/redzone.service
cp "$APP/deploy/redzone-backup.service" "$APP/deploy/redzone-backup.timer" /etc/systemd/system/
sed "s|{{HOST}}|$HOST|" "$APP/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now redzone
systemctl enable --now redzone-backup.timer
systemctl reload caddy || systemctl restart caddy

# ---------- 6. check ----------
echo "==> Waiting for REDZONE to start"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then echo "    REDZONE is running."; break; fi
  sleep 2
done
cat <<MSG

Done. Open:  https://$HOST
(The HTTPS certificate can take a minute the first time.)

Next steps:
  1. Add your keys:   sudo nano $ENVFILE     then: sudo systemctl restart redzone
  2. Logs:            sudo journalctl -u redzone -f
  3. Oracle Cloud:    also open ports 80 and 443 in the VCN security list (see docs/DEPLOY.md).
MSG

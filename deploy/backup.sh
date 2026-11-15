#!/usr/bin/env bash
# Daily database backup: stops REDZONE for a few seconds so the copy is consistent,
# keeps 14 days. Run by redzone-backup.timer (or by hand: sudo bash /opt/redzone/deploy/backup.sh).
set -euo pipefail
DIR=/opt/redzone/backups
STAMP="$(date -u +%Y%m%d_%H%M)"
mkdir -p "$DIR"
systemctl stop redzone
cp /opt/redzone/data/redzone.duckdb "$DIR/redzone_$STAMP.duckdb" || true
systemctl start redzone
gzip -f "$DIR/redzone_$STAMP.duckdb"
find "$DIR" -name 'redzone_*.duckdb.gz' -mtime +14 -delete
chown -R redzone:redzone "$DIR"
echo "backup: $DIR/redzone_$STAMP.duckdb.gz"

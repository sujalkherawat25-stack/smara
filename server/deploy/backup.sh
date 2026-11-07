#!/usr/bin/env bash
# Nightly Postgres dump for Smara Munim (cron: /etc/cron.d/munim-backup).
# Keeps KEEP_DAYS of gzipped dumps in BACKUP_ROOT; verifies each dump is readable.
set -euo pipefail
BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/munim}
KEEP_DAYS=${KEEP_DAYS:-7}
umask 077
mkdir -p "$BACKUP_ROOT"
out="$BACKUP_ROOT/munim_$(date -u +%Y%m%d_%H%M%S).sql.gz"
docker exec munim_postgres pg_dump -U munim --no-owner munim | gzip > "$out.tmp"
gzip -t "$out.tmp"
mv "$out.tmp" "$out"
sha256sum "$out" > "$out.sha256"
# Off-server copy: a backup on the same machine is lost with the machine. Set OFFSITE_RECIPIENT (an age public key)
# and OFFSITE_DEST (an rclone remote such as "b2:munim-backups") and each dump is encrypted, then copied out.
if [ -n "${OFFSITE_RECIPIENT:-}" ] && [ -n "${OFFSITE_DEST:-}" ]; then
  age -r "$OFFSITE_RECIPIENT" -o "$out.age" "$out"
  rclone copyto "$out.age" "$OFFSITE_DEST/$(basename "$out").age"
  rm -f "$out.age"
  echo "[munim-backup] offsite copy ok -> $OFFSITE_DEST"
else
  echo "[munim-backup] WARNING: no offsite copy configured (OFFSITE_RECIPIENT / OFFSITE_DEST)"
fi
find "$BACKUP_ROOT" -name 'munim_*.sql.gz*' -mtime +"$KEEP_DAYS" -delete
echo "[munim-backup] $(date -u +%FT%TZ) ok $(du -h "$out" | cut -f1) $out"

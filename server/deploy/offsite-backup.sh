#!/usr/bin/env bash
# Off-server nightly backup of the Smara Postgres DB.
#   dump (inside smara_postgres, read-only) -> gzip -> gpg encrypt to the PUBLIC key -> copy to R2 -> verify size -> retention.
# Installed on the Oracle host as /usr/local/bin/smara-offsite-backup.sh, run by /etc/cron.d/smara-offsite-backup (root).
# The server never holds a private key: old backups cannot be decrypted with server files alone. See server/docs/RESTORE.md.
# Config (root-only, not in git): /etc/smara-offsite/offsite.env  -> OFFSITE_DEST, GPG_RECIPIENT, optional RCLONE_CONFIG.
# Alerts: TELEGRAM_BOT_TOKEN and OPS_TELEGRAM_CHAT_ID are read from the app .env; values are never printed.
set -euo pipefail
CONF=${OFFSITE_CONF:-/etc/smara-offsite/offsite.env}
if [ -r "$CONF" ]; then . "$CONF"; fi
: "${OFFSITE_DEST:?OFFSITE_DEST not set (rclone remote path, e.g. r2:smara-offsite-backups)}"
: "${GPG_RECIPIENT:?GPG_RECIPIENT not set (fingerprint of the backup public key)}"
APP_ENV=${APP_ENV:-/home/ubuntu/smara/.env}
DAILY_KEEP=${DAILY_KEEP:-14}
WEEKLY_KEEP=${WEEKLY_KEEP:-8}
RCLONE=${RCLONE:-rclone}
STATE_DIR=${STATE_DIR:-/var/lib/smara-offsite}
umask 077
mkdir -p "$STATE_DIR"
WORK=$(mktemp -d /var/tmp/smara-offsite.XXXXXX)
STEP=start

env_get() { grep -E "^$1=" "$APP_ENV" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"\r' || true; }

alert() {  # one Telegram message to the ops chat; failures here never hide the original error
  local token chat
  token=$(env_get TELEGRAM_BOT_TOKEN); chat=$(env_get OPS_TELEGRAM_CHAT_ID)
  if [ -z "$token" ] || [ -z "$chat" ]; then echo "alert skipped: no ops telegram config"; return 0; fi
  curl -s -m 20 -o /dev/null --data-urlencode "chat_id=$chat" --data-urlencode "text=$1" \
    "https://api.telegram.org/bot${token}/sendMessage" || echo "alert send failed"
}

on_exit() {
  local rc=$?
  rm -rf "$WORK"
  if [ "$rc" -ne 0 ]; then
    echo "[smara-offsite] $(date -u +%FT%TZ) FAILED at step=$STEP exit=$rc"
    alert "Smara off-server backup FAILED at step '$STEP' on $(hostname) ($(date -u +%FT%TZ)). Check /var/log/smara-offsite-backup.log."
  fi
}
trap on_exit EXIT

STEP=dump
stamp=$(date -u +%Y%m%d_%H%M%S)
name="smara_${stamp}.sql.gz.gpg"
docker exec smara_postgres pg_dump -U smara --no-owner smara | gzip > "$WORK/db.sql.gz"
gzip -t "$WORK/db.sql.gz"
dump_bytes=$(stat -c %s "$WORK/db.sql.gz")
[ "$dump_bytes" -gt 1000 ] || { echo "dump suspiciously small ($dump_bytes bytes)"; exit 1; }

STEP=encrypt
gpg --batch --yes --trust-model always --recipient "$GPG_RECIPIENT" --output "$WORK/$name" --encrypt "$WORK/db.sql.gz"
rm -f "$WORK/db.sql.gz"
local_bytes=$(stat -c %s "$WORK/$name")

STEP=upload
"$RCLONE" copyto --checksum "$WORK/$name" "$OFFSITE_DEST/$name"

STEP=verify
remote_bytes=$("$RCLONE" lsf --format s --separator ';' "$OFFSITE_DEST/" --include "$name" | head -1 | tr -d ';' | tr -d '[:space:]')
if [ "$remote_bytes" != "$local_bytes" ]; then
  echo "size mismatch after upload: local=$local_bytes remote=${remote_bytes:-missing}"; exit 1
fi

STEP=retention
# Names sort by time, so newest first = reverse sort. Keep the newest DAILY_KEEP, plus the newest WEEKLY_KEEP Sundays.
# Never delete when the listing is empty or fails (the upload just verified, so the list cannot legitimately be empty).
mapfile -t all < <("$RCLONE" lsf "$OFFSITE_DEST/" | grep -E '^smara_[0-9]{8}_[0-9]{6}\.sql\.gz\.gpg$' | sort -r)
[ "${#all[@]}" -ge 1 ] || { echo "listing returned no backups; retention skipped"; exit 1; }
sundays=0; deleted=0
for i in "${!all[@]}"; do
  f=${all[$i]}; d=${f:6:8}; keep=0
  [ "$i" -lt "$DAILY_KEEP" ] && keep=1
  if [ "$(date -u -d "$d" +%u)" = 7 ]; then
    sundays=$((sundays+1))
    [ "$sundays" -le "$WEEKLY_KEEP" ] && keep=1
  fi
  if [ "$keep" -eq 0 ]; then
    "$RCLONE" deletefile "$OFFSITE_DEST/$f" && deleted=$((deleted+1))
  fi
done
echo "$stamp" > "$STATE_DIR/last_success"
echo "[smara-offsite] $(date -u +%FT%TZ) ok $name size=${local_bytes}B remote=${remote_bytes}B objects=${#all[@]} pruned=$deleted"

#!/usr/bin/env bash
# PC side of the off-server backup (until an S3-compatible bucket is set up). Runs on the owner's Windows PC daily
# via Task Scheduler (see server/docs/RESTORE.md). It:
#   1. lists encrypted backups waiting in the server outbox (created by /usr/local/bin/smara-offsite-backup.sh),
#   2. copies each one down over the existing SSH key, checks its SHA-256 against the server's, and moves it into the local archive,
#   3. deletes the server's copy only after the local copy is verified,
#   4. prunes the local archive to 14 daily + 8 weekly (Sunday) copies,
#   5. on any failure, sends one Telegram message to the ops chat (token read from the server .env, never printed).
# The archive holds ENCRYPTED files only. The private key is not on this PC unless the owner imports it for a restore test.
set -uo pipefail
KEY=${SMARA_SSH_KEY:-/c/Users/sujal/Downloads/ssh-key-2026-05-29.key}
HOST=${SMARA_SSH_HOST:-ubuntu@140.245.251.13}
OUTBOX=${SMARA_OUTBOX:-/home/ubuntu/smara-offsite-outbox}
ARCHIVE=${SMARA_ARCHIVE:-/c/Users/sujal/smara-offsite-archive}
LOG=${SMARA_PULL_LOG:-/c/Users/sujal/smara-offsite-archive/pull.log}
DAILY_KEEP=14; WEEKLY_KEEP=8
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=20 $HOST"
mkdir -p "$ARCHIVE"; umask 077
say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }
FAILED=0
alert() {
  local tok chat
  tok=$($SSH "grep -E '^TELEGRAM_BOT_TOKEN=' /home/ubuntu/smara/.env | tail -1 | cut -d= -f2- | tr -d '\"\r'" 2>/dev/null)
  chat=$($SSH "grep -E '^OPS_TELEGRAM_CHAT_ID=' /home/ubuntu/smara/.env | tail -1 | cut -d= -f2- | tr -d '\"\r'" 2>/dev/null)
  [ -n "$tok" ] && [ -n "$chat" ] || { say "alert skipped: no ops telegram config"; return; }
  curl -s -m 20 -o /dev/null --data-urlencode "chat_id=$chat" --data-urlencode "text=$1" \
    "https://api.telegram.org/bot${tok}/sendMessage" || say "alert send failed"
}
fail() { say "FAILED: $1"; FAILED=1; }

names=$($SSH "ls -1 $OUTBOX 2>/dev/null | grep -E '^smara_[0-9]{8}_[0-9]{6}\.sql\.gz\.gpg$' | sort" 2>/dev/null) || { fail "cannot list server outbox (ssh)"; }
count=0
for name in $names; do
  part="$ARCHIVE/$name.part"
  if [ -f "$ARCHIVE/$name" ]; then
    # Already archived. Make sure the server copy is removed only when the local copy matches.
    if [ "$(sha256sum "$ARCHIVE/$name" | cut -d' ' -f1)" = "$($SSH "cat $OUTBOX/$name.sha256" 2>/dev/null)" ]; then
      $SSH "rm -f $OUTBOX/$name $OUTBOX/$name.sha256" && say "removed server copy of already archived $name"
    fi
    continue
  fi
  scp -q -i "$KEY" -o BatchMode=yes "$HOST:$OUTBOX/$name" "$part" || { fail "scp $name"; rm -f "$part"; continue; }
  want=$($SSH "cat $OUTBOX/$name.sha256" 2>/dev/null)
  got=$(sha256sum "$part" | cut -d' ' -f1)
  if [ -z "$want" ] || [ "$got" != "$want" ]; then fail "sha256 mismatch for $name (want=${want:-none} got=$got)"; rm -f "$part"; continue; fi
  mv "$part" "$ARCHIVE/$name"
  $SSH "rm -f $OUTBOX/$name $OUTBOX/$name.sha256" || fail "could not remove server copy of $name (archived locally, safe)"
  count=$((count+1)); say "archived $name bytes=$(stat -c %s "$ARCHIVE/$name") sha256=$got"
done
[ -n "$names" ] || say "no new backups waiting on the server"

# Retention on the local archive (same policy as the bucket version): newest 14 + newest 8 Sundays.
mapfile -t all < <(ls -1 "$ARCHIVE" 2>/dev/null | grep -E '^smara_[0-9]{8}_[0-9]{6}\.sql\.gz\.gpg$' | sort -r)
sundays=0; pruned=0
for i in "${!all[@]}"; do
  f=${all[$i]}; d=${f:6:8}; keep=0
  [ "$i" -lt "$DAILY_KEEP" ] && keep=1
  if [ "$(date -u -d "$d" +%u)" = 7 ]; then sundays=$((sundays+1)); [ "$sundays" -le "$WEEKLY_KEEP" ] && keep=1; fi
  if [ "$keep" -eq 0 ]; then rm -f "$ARCHIVE/$f" && pruned=$((pruned+1)); fi
done
say "archive: ${#all[@]} copies, pruned $pruned, newly archived $count"

if [ "$FAILED" -ne 0 ]; then
  alert "Smara off-server backup pull FAILED on $(hostname) at $(date -u +%FT%TZ). See $LOG."
fi
exit "$FAILED"

#!/usr/bin/env sh
set -eu
umask 077

# Run from a deployed Smara checkout. BACKUP_DIR must be durable storage, not
# the application container's writable layer. This produces a portable custom
# pg_dump archive and a manifest suitable for restore drills.
: "${BACKUP_DIR:?Set BACKUP_DIR to a durable backup directory}"
project="${SMARA_COMPOSE_PROJECT:-${COMPOSE_PROJECT_NAME:-smara}}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
docker compose -p "$project" exec -T postgres pg_dump -U smara -d smara -Fc > "$BACKUP_DIR/smara-$stamp.dump"
sha256sum "$BACKUP_DIR/smara-$stamp.dump" > "$BACKUP_DIR/smara-$stamp.dump.sha256"
find "$BACKUP_DIR" -type f -name 'smara-*.dump' -mtime +7 -delete
echo "Created $BACKUP_DIR/smara-$stamp.dump"

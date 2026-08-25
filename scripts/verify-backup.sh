#!/usr/bin/env sh
set -eu

# Non-destructive integrity check. A full restore drill uses a disposable
# Postgres database/volume, never the live Smara database.
: "${1:?Pass the backup .dump file}"
project="${SMARA_COMPOSE_PROJECT:-${COMPOSE_PROJECT_NAME:-smara}}"
sha256sum -c "$1.sha256"
docker compose -p "$project" exec -T postgres pg_restore --list < "$1" >/dev/null
echo "Backup archive is readable. Perform the documented disposable restore drill before release."

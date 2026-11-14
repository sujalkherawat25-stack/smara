#!/usr/bin/env bash
# Restore test: decrypt one off-server backup and restore it into a THROWAWAY Postgres container.
# Never connects to smara_postgres or the production database. Prints row counts to compare with production.
#   GNUPGHOME=/path/to/keyring-with-private-key  bash server/deploy/restore-check.sh <smara_YYYYMMDD_HHMMSS.sql.gz.gpg> [container] [port]
# The private key is needed here and must NOT be stored on the server.
set -euo pipefail
IN=${1:?usage: restore-check.sh <backup.sql.gz.gpg> [container-name] [host-port]}
NAME=${2:-smara-restore-test}
PORT=${3:-25433}
[ "$NAME" != smara_postgres ] || { echo "refusing to use the production container name"; exit 1; }
WORK=$(mktemp -d)
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT

gpg --batch --decrypt --output "$WORK/db.sql.gz" "$IN"
gzip -t "$WORK/db.sql.gz"
echo "decrypted ok: $(stat -c %s "$WORK/db.sql.gz" 2>/dev/null || wc -c < "$WORK/db.sql.gz") bytes gzip"

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -e POSTGRES_USER=smara -e POSTGRES_DB=smara -e POSTGRES_HOST_AUTH_METHOD=trust \
  -p "127.0.0.1:${PORT}:5432" postgres:16-alpine >/dev/null
for _ in $(seq 1 60); do docker exec "$NAME" pg_isready -U smara -d smara >/dev/null 2>&1 && break; sleep 1; done
# pg_dump --no-owner does not include cluster roles, but GRANT statements name them. Create each one as NOLOGIN
# in this throwaway container only, so the restore can run.
roles=$(gunzip -c "$WORK/db.sql.gz" | grep -oE '^GRANT .* TO [a-zA-Z_][a-zA-Z0-9_]*' | awk '{print $NF}' | sort -u || true)
for r in $roles; do
  docker exec "$NAME" psql -q -U smara -d smara -c "do \$\$ begin if not exists (select 1 from pg_roles where rolname='$r') then create role \"$r\" nologin; end if; end \$\$;" >/dev/null
  echo "created role for restore: $r"
done
gunzip -c "$WORK/db.sql.gz" | docker exec -i "$NAME" psql -q -U smara -d smara -v ON_ERROR_STOP=1 >/dev/null
echo "restored into $NAME (127.0.0.1:$PORT). Row counts:"
docker exec "$NAME" psql -U smara -d smara -tA -F' ' -c "
  select 'tenants', count(*) from tenants
  union all select 'parties', count(*) from parties
  union all select 'obligations', count(*) from obligations
  union all select 'chat_messages', count(*) from chat_messages;"

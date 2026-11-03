#!/usr/bin/env bash
# Deploy a release tarball on the Oracle host (run from /home/ubuntu/smara-munim).
#   bash deploy/release.sh /tmp/munim-src.tar.gz <version>
# Backs up the DB first, keeps .env, rebuilds app + sandbox, waits for health.
set -euo pipefail
TARBALL=$1; VERSION=$2
cd "$(dirname "$0")/.."
sudo BACKUP_ROOT=/var/backups/munim bash deploy/backup.sh
tar xzf "$TARBALL" && rm -f "$TARBALL"
export MUNIM_VERSION=$VERSION
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d --build munim_sandbox munim_app
for i in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' munim_app 2>/dev/null)" = healthy ] && break; sleep 3
done
docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep munim
curl -s localhost:8090/healthz; echo

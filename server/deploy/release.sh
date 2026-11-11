#!/usr/bin/env bash
# Deploy a release tarball on the Oracle host (run from /home/ubuntu/smara-munim).
#   bash deploy/release.sh /tmp/munim-src.tar.gz <version>
# Backs up the DB first, keeps .env, rebuilds app + sandbox, waits for health, then
# prunes old release images and build cache so the disk does not fill up (each app
# image is ~2 GB). The running image and the previous one are always kept for rollback.
set -euo pipefail
TARBALL=$1; VERSION=$2
cd "$(dirname "$0")/.."
sudo BACKUP_ROOT=/var/backups/munim bash deploy/backup.sh
tar xzf "$TARBALL" && rm -f "$TARBALL"
# Vault master key: created once, kept outside the release tree's tracked files. Back it up offline: without it the
# shops' encrypted secrets cannot be read.
if [ ! -s secrets/vault_master_key ]; then
  (umask 077; mkdir -p secrets; python3 -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())' > secrets/vault_master_key)
  echo "created a new vault master key in secrets/vault_master_key - back it up offline" >&2
fi
PREVIOUS=$(docker inspect -f '{{.Config.Image}}' munim_app 2>/dev/null | cut -d: -f2 || true)
export MUNIM_VERSION=$VERSION
# The WhatsApp link sidecar is off unless COMPOSE_PROFILES=wa-link is set (see the compose file).
if [ "${COMPOSE_PROFILES:-}" = "wa-link" ]; then WA_SVC=munim_wa_link; else WA_SVC=; fi
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d --build munim_sandbox $WA_SVC munim_app
for i in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' munim_app 2>/dev/null)" = healthy ] && break; sleep 3
done
if [ "$(docker inspect -f '{{.State.Health.Status}}' munim_app)" != healthy ]; then
  echo "app did not become healthy; keeping old images for rollback (previous: ${PREVIOUS:-none})" >&2; exit 1
fi
# prune: keep this version and the previous one
for repo in smara-munim smara-munim-sandbox; do
  docker images --format '{{.Tag}}' "$repo" | grep -vxE "$VERSION|${PREVIOUS:-none}" | while read -r tag; do
    docker rmi "$repo:$tag" >/dev/null 2>&1 || true
  done
done
docker builder prune -f --keep-storage 2GB >/dev/null 2>&1 || true
docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep munim
curl -s localhost:8090/healthz; echo
df -h / | tail -1

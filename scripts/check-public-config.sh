#!/usr/bin/env sh
set -eu

# Deployment guard for the canonical Smara VM. It catches the easy-to-miss
# failure where a valid Caddyfile is loaded without the marketing host block.
# The script is safe to run locally: set CADDYFILE to a checkout path and
# leave CHECK_PUBLIC empty to skip network probes.
CADDYFILE=${CADDYFILE:-/etc/caddy/Caddyfile}
CHECK_PUBLIC=${CHECK_PUBLIC:-false}

if [ ! -f "$CADDYFILE" ]; then
  printf 'CADDYFILE=missing (%s)\n' "$CADDYFILE"
  exit 1
fi

required='syntarus.com, www.syntarus.com {|reverse_proxy 127.0.0.1:8082|ai.syntarus.com {|handle_path /smara-api/*|reverse_proxy 127.0.0.1:8090|reverse_proxy 127.0.0.1:8081'
old_ifs=$IFS
IFS='|'
for needle in $required; do
  if ! grep -Fq "$needle" "$CADDYFILE"; then
    printf 'CADDY_REQUIRED=missing (%s)\n' "$needle"
    IFS=$old_ifs
    exit 1
  fi
done
IFS=$old_ifs

if command -v caddy >/dev/null 2>&1; then
  # Caddy validates configured log destinations too; on the VM those files
  # are root-owned, so use sudo when this guard is run as the deploy user.
  if [ "$(id -u)" -eq 0 ]; then
    caddy validate --config "$CADDYFILE" >/dev/null
  elif command -v sudo >/dev/null 2>&1; then
    sudo caddy validate --config "$CADDYFILE" >/dev/null
  else
    caddy validate --config "$CADDYFILE" >/dev/null
  fi
fi
printf 'CADDY_CONFIG=PASS\n'

if [ "$CHECK_PUBLIC" = true ]; then
  for url in \
    https://syntarus.com/ \
    https://www.syntarus.com/ \
    https://ai.syntarus.com/ \
    https://ai.syntarus.com/smara-api/health; do
    curl --fail --silent --show-error --max-time 20 "$url" >/dev/null
    printf 'PUBLIC_OK=%s\n' "$url"
  done
fi

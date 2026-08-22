#!/usr/bin/env sh
set -eu

# Configuration gate only: it checks presence and safe values, never prints
# secret contents. Run it inside the deployed API image with the deployment
# environment loaded. Use --full for the complete feature set.
full=false
if [ "${1:-}" = "--full" ]; then full=true; fi

fail=0
required="SMARA_DATABASE_URL SMARA_POSTGRES_PASSWORD SMARA_REDIS_URL SMARA_GATEWAY_SIGNING_SECRET SMARA_CONTROL_BRIDGE_SECRET SMARA_INTEGRATION_MASTER_KEYS SYNTARUS_API_KEY SMARA_PUBLIC_BASE_URL"
for key in $required; do
  eval "value=\${$key:-}"
  if [ -z "$value" ]; then
    printf '%s=missing\n' "$key"
    fail=1
  else
    printf '%s=configured\n' "$key"
  fi
done

if [ "${SMARA_DEV_MODE:-false}" = "true" ]; then
  echo 'SMARA_DEV_MODE must be false outside local development.'
  fail=1
fi
case "${SMARA_PUBLIC_BASE_URL:-}" in
  http://127.*|http://localhost*|*example.com*)
    echo 'SMARA_PUBLIC_BASE_URL must be the real HTTPS deployment URL.'
    fail=1 ;;
esac

if [ "$full" = true ]; then
  for key in SMARA_CLI_TOKEN_SECRET SMARA_SENTRY_DSN SMARA_VAPID_PUBLIC_KEY SMARA_VAPID_PRIVATE_KEY; do
    eval "value=\${$key:-}"
    if [ -z "$value" ]; then
      printf '%s=missing (full gate)\n' "$key"
      fail=1
    else
      printf '%s=configured\n' "$key"
    fi
  done
fi

if [ "$fail" -ne 0 ]; then
  echo 'PRODUCTION_CONFIG=FAIL'
  exit 1
fi
echo 'PRODUCTION_CONFIG=PASS'

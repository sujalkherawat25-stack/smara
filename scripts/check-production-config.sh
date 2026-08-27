#!/usr/bin/env sh
set -eu

# Configuration gate only: it checks presence and safe values, never prints
# secret contents. Run it inside the deployed API image with the deployment
# environment loaded. Use --full for the complete feature set.
full=false
if [ "${1:-}" = "--full" ]; then full=true; fi

fail=0
present() {
  key="$1"
  eval "value=\${$key:-}"
  if [ -n "$value" ]; then return 0; fi
  file_key="${key}_FILE"
  eval "file=\${$file_key:-}"
  [ -n "$file" ] && [ -s "$file" ]
}
required="SMARA_DATABASE_URL SMARA_POSTGRES_PASSWORD SMARA_REDIS_URL SMARA_GATEWAY_SIGNING_SECRET SMARA_CONTROL_BRIDGE_SECRET SYNTARUS_API_KEY SMARA_PUBLIC_BASE_URL"
for key in $required; do
  if ! present "$key"; then
    printf '%s=missing\n' "$key"
    fail=1
  else
    printf '%s=configured\n' "$key"
  fi
done

# User-account integrations are deliberately local-only on the hosted control
# plane. Their vault key is required only for the explicitly opt-in legacy
# hosted mode; the default deployment must not require or receive it.
if [ "${SMARA_HOSTED_USER_INTEGRATIONS_ENABLED:-false}" = "true" ]; then
  if ! present "SMARA_INTEGRATION_MASTER_KEYS"; then
    printf '%s=missing (hosted user integrations enabled)\n' "SMARA_INTEGRATION_MASTER_KEYS"
    fail=1
  else
    printf '%s=configured (hosted user integrations enabled)\n' "SMARA_INTEGRATION_MASTER_KEYS"
  fi
else
  printf '%s=disabled (personal actions stay on paired desktop)\n' "SMARA_HOSTED_USER_INTEGRATIONS_ENABLED"
fi

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
    if ! present "$key"; then
      printf '%s=missing (full gate)\n' "$key"
      fail=1
    else
      printf '%s=configured\n' "$key"
    fi
  done
  # A production agent must have either the legacy provider triple or at least
  # one operator-defined profile. Keys may be supplied through *_FILE values.
  if ! present SMARA_LLM_PROFILES && ! present SMARA_LLM_BASE_URL; then
    echo 'LLM provider is missing: configure SMARA_LLM_PROFILES or SMARA_LLM_BASE_URL/SMARA_LLM_API_KEY/SMARA_LLM_MODEL.'
    fail=1
  elif ! present SMARA_LLM_PROFILES; then
    for key in SMARA_LLM_API_KEY SMARA_LLM_MODEL; do
      if ! present "$key"; then
        printf '%s=missing (LLM provider)\n' "$key"
        fail=1
      fi
    done
  fi
  if [ "${SMARA_SANDBOX_ENABLED:-false}" = "true" ]; then
    for key in SMARA_SANDBOX_URL SMARA_SANDBOX_TOKEN; do
      if ! present "$key"; then
        printf '%s=missing (sandbox enabled)\n' "$key"
        fail=1
      else
        printf '%s=configured\n' "$key"
      fi
    done
  fi
fi

if [ "$fail" -ne 0 ]; then
  echo 'PRODUCTION_CONFIG=FAIL'
  exit 1
fi
echo 'PRODUCTION_CONFIG=PASS'

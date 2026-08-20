ALTER TABLE integration_action_log
  ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE integration_action_log
  ADD COLUMN IF NOT EXISTS approval_note TEXT NOT NULL DEFAULT '';
ALTER TABLE integration_action_log
  ADD COLUMN IF NOT EXISTS lease_owner TEXT;
ALTER TABLE integration_action_log
  ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE integration_action_log
  ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE integration_action_log
  ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE integration_action_log
  ADD COLUMN IF NOT EXISTS result_summary TEXT;
ALTER TABLE integration_action_log DROP CONSTRAINT IF EXISTS integration_action_log_status_check;
ALTER TABLE integration_action_log
  ADD CONSTRAINT integration_action_log_status_check
  CHECK (status IN ('blocked','draft','awaiting_approval','approved','denied','running','completed','failed'));
CREATE TABLE IF NOT EXISTS integration_credentials (
  connection_id TEXT PRIMARY KEY REFERENCES integration_connections(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('oauth_token','bot_token','api_token')),
  encrypted_secret TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS integration_oauth_states (
  state_hash TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  code_verifier TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS integration_actions_ready ON integration_action_log(status, lease_expires_at, created_at);

ALTER TABLE integration_action_log
  ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE integration_action_log
  ADD COLUMN IF NOT EXISTS retry_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS integration_actions_due
  ON integration_action_log(status, retry_at, created_at);

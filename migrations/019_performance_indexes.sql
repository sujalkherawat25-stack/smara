-- Hot-path indexes for the hosted task worker and conversation API.
-- Every statement is idempotent so this migration is safe on existing data.
CREATE INDEX IF NOT EXISTS task_steps_ready_idx
  ON task_steps(status, retry_at, executor_kind, task_id, ordinal);
CREATE INDEX IF NOT EXISTS task_events_task_created_idx
  ON task_events(task_id, created_at, id);
CREATE INDEX IF NOT EXISTS tasks_account_updated_idx
  ON tasks(account_id, updated_at);
CREATE INDEX IF NOT EXISTS executor_leases_active_idx
  ON executor_leases(step_id, executor_id, completed_at, expires_at);
CREATE INDEX IF NOT EXISTS integration_action_ready_idx
  ON integration_action_log(status, retry_at, created_at);

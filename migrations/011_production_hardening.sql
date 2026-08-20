-- Terminal task failures are retained for explicit review rather than being
-- silently lost after the retry budget is exhausted.
CREATE TABLE IF NOT EXISTS task_dead_letters (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  step_id TEXT NOT NULL REFERENCES task_steps(id) ON DELETE CASCADE,
  account_id TEXT NOT NULL,
  error TEXT NOT NULL,
  attempts INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS task_dead_letters_account_created
  ON task_dead_letters(account_id, created_at DESC);

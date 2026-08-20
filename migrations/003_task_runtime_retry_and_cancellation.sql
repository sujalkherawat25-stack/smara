-- Safe for databases created from older 001/002 migrations.
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS retry_at TIMESTAMPTZ;
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_status_check
  CHECK (status IN ('queued','running','waiting_approval','cancelling','completed','failed','cancelled'));

CREATE TABLE IF NOT EXISTS approvals (
  task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('approved','denied')),
  note TEXT NOT NULL,
  decided_at TIMESTAMPTZ
);

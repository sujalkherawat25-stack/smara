ALTER TABLE task_steps
  ADD COLUMN IF NOT EXISTS executor_payload JSONB NOT NULL DEFAULT '{}'::jsonb;

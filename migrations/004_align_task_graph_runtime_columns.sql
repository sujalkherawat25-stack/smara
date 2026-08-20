-- Align early schema drafts with the shared SQLite/Postgres task-store contract.
ALTER TABLE task_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS task_id TEXT;
ALTER TABLE task_steps ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
UPDATE task_steps s SET task_id = r.task_id FROM task_runs r
  WHERE s.task_run_id = r.id AND s.task_id IS NULL;
ALTER TABLE task_steps ALTER COLUMN task_id SET NOT NULL;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'task_steps_task_fk') THEN
    ALTER TABLE task_steps ADD CONSTRAINT task_steps_task_fk
      FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE;
  END IF;
END $$;

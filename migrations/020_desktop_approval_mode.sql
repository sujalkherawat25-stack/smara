-- The hosted service can create and audit local work, but a paired desktop
-- is the only authority that may release it for execution.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS approval_mode TEXT NOT NULL DEFAULT 'hosted';

UPDATE tasks
   SET approval_mode = 'desktop'
 WHERE approval_mode = 'hosted'
   AND EXISTS (
       SELECT 1
         FROM task_steps s
        WHERE s.task_id = tasks.id
          AND s.executor_kind = 'desktop'
   );

ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_approval_mode_check;
ALTER TABLE tasks
    ADD CONSTRAINT tasks_approval_mode_check
    CHECK (approval_mode IN ('hosted', 'desktop'));

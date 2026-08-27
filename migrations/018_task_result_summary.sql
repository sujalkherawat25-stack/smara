-- Keep the final user-facing task result on the task row, not only in an
-- event. This makes list/get APIs self-contained and lets every client render
-- the result without guessing which event is terminal.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS result_summary TEXT;

-- Backfill useful results written by earlier workers. The old "recorded"
-- marker was bookkeeping only and must never become the displayed answer.
WITH ranked AS (
    SELECT DISTINCT ON (task_id)
        task_id,
        NULLIF(BTRIM(payload->>'result'), '') AS result
    FROM task_events
    WHERE type IN ('step.completed', 'task.completed')
      AND jsonb_typeof(payload) = 'object'
      AND NULLIF(BTRIM(payload->>'result'), '') IS NOT NULL
      AND LOWER(BTRIM(payload->>'result')) NOT IN ('recorded', 'completed', 'succeeded', 'success', 'ok')
    ORDER BY task_id, created_at DESC, id DESC
)
UPDATE tasks AS t
SET result_summary = ranked.result
FROM ranked
WHERE t.id = ranked.task_id
  AND (t.result_summary IS NULL OR BTRIM(t.result_summary) = '');

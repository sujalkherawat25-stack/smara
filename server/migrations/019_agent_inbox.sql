-- Owner messages that arrive while a run is working on a task. They wait here and the run reads them at its next
-- step (agent_loop.py). Each entry: {"content": text, "files": [file refs]}. Cleared when read.
ALTER TABLE agent_sessions ADD COLUMN inbox JSONB NOT NULL DEFAULT '[]';

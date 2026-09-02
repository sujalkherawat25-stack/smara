-- Explicit account facts are a small reliability index for shared memory.
-- They contain only user-stated profile values; the full semantic history
-- continues to live in Syntarus through Smara's SDK boundary.
CREATE TABLE IF NOT EXISTS account_memory_facts (
    account_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'explicit_user_statement',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(account_id, workspace_id, fact_key)
);

CREATE INDEX IF NOT EXISTS account_memory_facts_account_updated
  ON account_memory_facts(account_id, updated_at DESC);

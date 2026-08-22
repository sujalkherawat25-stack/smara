CREATE TABLE IF NOT EXISTS cli_pairings (
    code_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cli_pairings_active
    ON cli_pairings (expires_at) WHERE consumed_at IS NULL;

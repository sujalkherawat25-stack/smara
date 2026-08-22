CREATE TABLE IF NOT EXISTS cli_device_requests (
    device_code_hash TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    account_id TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    approved_at TIMESTAMPTZ,
    consumed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cli_device_requests_active
    ON cli_device_requests (expires_at) WHERE consumed_at IS NULL;

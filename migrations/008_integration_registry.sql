CREATE TABLE IF NOT EXISTS integration_connections (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  provider TEXT NOT NULL CHECK (provider IN ('gmail','calendar','telegram','github','drive')),
  display_name TEXT NOT NULL DEFAULT '',
  policy TEXT NOT NULL CHECK (policy IN ('observe','draft','assisted','trusted','blocked')),
  granted_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
  health TEXT NOT NULL CHECK (health IN ('not_connected','healthy','needs_reauth','error')) DEFAULT 'not_connected',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(account_id, provider)
);
CREATE TABLE IF NOT EXISTS integration_action_log (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  connection_id TEXT NOT NULL REFERENCES integration_connections(id) ON DELETE CASCADE,
  action TEXT NOT NULL,
  preview TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  risk TEXT NOT NULL CHECK (risk IN ('read','external')),
  status TEXT NOT NULL CHECK (status IN ('blocked','draft','awaiting_approval')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(account_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS integration_connections_account ON integration_connections(account_id, provider);
CREATE INDEX IF NOT EXISTS integration_action_log_account ON integration_action_log(account_id, created_at DESC);

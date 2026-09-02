CREATE TABLE IF NOT EXISTS skills (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('draft','tested','published','deprecated')),
  fingerprint TEXT NOT NULL,
  tested BOOLEAN NOT NULL DEFAULT FALSE,
  test_run_id TEXT,
  approved_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(account_id, name, version)
);

CREATE INDEX IF NOT EXISTS skills_account_updated_idx ON skills(account_id, updated_at);

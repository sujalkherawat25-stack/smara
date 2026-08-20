CREATE TABLE IF NOT EXISTS research_evidence (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  title TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending','fetched','verified','failed','blocked')),
  retrieved_at TIMESTAMPTZ,
  content_sha256 TEXT,
  excerpt TEXT,
  claim TEXT,
  confidence DOUBLE PRECISION,
  citation_label TEXT,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(task_id, url)
);
CREATE INDEX IF NOT EXISTS research_evidence_task_created ON research_evidence(task_id, created_at);

ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS content TEXT;

ALTER TABLE research_evidence ADD COLUMN IF NOT EXISTS published_at TEXT;
ALTER TABLE research_evidence ADD COLUMN IF NOT EXISTS domain_policy TEXT NOT NULL DEFAULT 'unclassified';
ALTER TABLE research_evidence ADD COLUMN IF NOT EXISTS quality_flags TEXT NOT NULL DEFAULT '[]';
ALTER TABLE research_evidence ADD COLUMN IF NOT EXISTS agreement_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE research_evidence ADD COLUMN IF NOT EXISTS verification_notes TEXT;

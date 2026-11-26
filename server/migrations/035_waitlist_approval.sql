-- One-step approval: an admin lets a waitlisted number in and its account is made at once (no code).
ALTER TABLE waitlist ADD COLUMN IF NOT EXISTS approved_at timestamptz;       -- when an admin approved it
ALTER TABLE waitlist ADD COLUMN IF NOT EXISTS name text NOT NULL DEFAULT '';  -- the name their WhatsApp profile shows
-- when they last wrote: WhatsApp delivers an ordinary message only within 24 hours of that
ALTER TABLE waitlist ADD COLUMN IF NOT EXISTS last_seen_at timestamptz;
UPDATE waitlist SET last_seen_at = greatest(created_at, replied_at) WHERE last_seen_at IS NULL;

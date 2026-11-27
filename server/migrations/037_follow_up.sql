-- One follow-up after someone goes quiet (src/loop/checkin.py): which quiet spell was already looked at (their last
-- message time), when one was last sent, and whether they turned it off.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS checkin_for timestamptz;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS checkin_sent_at timestamptz;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS checkin_off boolean NOT NULL DEFAULT false;

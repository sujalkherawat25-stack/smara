-- Which channel the owner last used, so approvals follow them (WhatsApp or Telegram).
-- owner_last_inbound_at keeps meaning "last WhatsApp message" (it drives the 24h window).
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS owner_last_channel text;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS owner_last_active_at timestamptz;

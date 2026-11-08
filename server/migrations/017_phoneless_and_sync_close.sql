-- Customers without a WhatsApp number are allowed (Tally and sheets often lack one). They are never messaged
-- until the owner adds a number. Uniqueness of a number applies only when there is one.
ALTER TABLE parties ALTER COLUMN whatsapp_phone DROP NOT NULL;
ALTER TABLE parties DROP CONSTRAINT IF EXISTS parties_tenant_id_whatsapp_phone_key;
CREATE UNIQUE INDEX IF NOT EXISTS parties_tenant_phone_uniq ON parties (tenant_id, whatsapp_phone) WHERE whatsapp_phone IS NOT NULL;

-- Where an invoice came from. Only TALLY invoices are closed automatically when a newer Tally snapshot drops them.
ALTER TABLE obligations ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'SHEET';

-- Each shop connects its OWN WhatsApp Business number through Meta's Embedded Signup.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_waba_id text;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_display_phone text;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wa_connected_at timestamptz;

-- One-time links the owner opens to connect a number (sent in their authenticated chat).
CREATE TABLE IF NOT EXISTS whatsapp_connect_links (
    token_hash  text PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenants(id),
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- What happened on each connect attempt (no tokens here; the token lives in the vault).
CREATE TABLE IF NOT EXISTS whatsapp_connections (
    id              bigserial PRIMARY KEY,
    tenant_id       uuid NOT NULL REFERENCES tenants(id),
    waba_id         text,
    phone_number_id text,
    mode            text,            -- coexistence (their existing app number) | cloud (new number)
    status          text NOT NULL,   -- connected | failed
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

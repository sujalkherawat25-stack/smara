-- A shop's own WhatsApp number, linked through the Baileys sidecar (server/wa_link; unofficial, see its README).
-- The link's credentials are NOT here: they are encrypted in tenant_secrets (name wa_link_auth) through the vault.

CREATE TABLE IF NOT EXISTS wa_links (
    tenant_id     uuid PRIMARY KEY REFERENCES tenants(id),
    status        text NOT NULL DEFAULT 'pairing',  -- pairing | connected | reconnecting | disconnected | logged_out
    phone         text NOT NULL DEFAULT '',         -- digits of the SIM's number, once linked
    detail        text NOT NULL DEFAULT '',
    connected_at  timestamptz,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Sends per shop per IST day. Incremented atomically before each send, so the daily cap cannot be overrun by a race.
CREATE TABLE IF NOT EXISTS wa_link_daily_sends (
    tenant_id  uuid NOT NULL REFERENCES tenants(id),
    send_date  date NOT NULL,
    sent       integer NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, send_date)
);

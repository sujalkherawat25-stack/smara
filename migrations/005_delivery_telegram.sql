-- Every outbound WhatsApp message we send (or hold), so Meta's delivery
-- receipts can be matched back to the obligation/claim they were about.
CREATE TABLE outbound_messages (
    msg_id         TEXT PRIMARY KEY,          -- Meta wamid, or held:<uuid> when not sent yet
    tenant_id      UUID NOT NULL,
    kind           TEXT NOT NULL,             -- owner_reminder_card | owner_claim_card | owner_text | owner_digest | debtor_reminder
    to_phone       TEXT NOT NULL,
    obligation_id  UUID,
    adjustment_id  UUID,
    status         TEXT NOT NULL CHECK (status IN ('held', 'sent', 'delivered', 'read', 'failed', 'resent')),
    error_code     INT,
    error_title    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX outbound_retry_idx ON outbound_messages (tenant_id) WHERE status IN ('held', 'failed');

-- Owner's 24h WhatsApp window, and the Telegram owner channel.
ALTER TABLE tenants
    ADD COLUMN owner_last_inbound_at   TIMESTAMPTZ,
    ADD COLUMN owner_nudged_at         TIMESTAMPTZ,
    ADD COLUMN telegram_chat_id        BIGINT UNIQUE,
    ADD COLUMN telegram_link_code      TEXT UNIQUE,
    ADD COLUMN telegram_link_expires_at TIMESTAMPTZ;

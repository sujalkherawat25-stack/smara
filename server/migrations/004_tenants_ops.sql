-- Tenants live in Postgres (was env maps), plus message dedupe, durable digest
-- state and the debtor's last inbound time (WhatsApp's 24-hour reply window).
CREATE TABLE tenants (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    owner_phone         TEXT NOT NULL UNIQUE,
    upi_vpa             TEXT,
    upi_name            TEXT,
    wa_phone_number_id  TEXT UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE processed_messages (
    wamid        TEXT PRIMARY KEY,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE digest_log (
    tenant_id    UUID NOT NULL,
    digest_date  DATE NOT NULL,
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, digest_date)
);

ALTER TABLE parties ADD COLUMN last_inbound_at TIMESTAMPTZ;
-- Set once the owner's approval card is sent, so a redelivery after a failed send re-notifies.
ALTER TABLE proposed_adjustments ADD COLUMN owner_notified_at TIMESTAMPTZ;

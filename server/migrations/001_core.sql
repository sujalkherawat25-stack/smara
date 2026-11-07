-- Postgres = What & When. Continuum = Why & Proof.

CREATE TABLE parties (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID NOT NULL,
    canonical_name       TEXT NOT NULL,
    aliases              TEXT[] NOT NULL DEFAULT '{}',
    whatsapp_phone       TEXT NOT NULL,
    gstin                TEXT,
    continuum_entity_id  TEXT,
    UNIQUE (tenant_id, whatsapp_phone)
);

CREATE TABLE obligations (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                 UUID NOT NULL,
    party_id                  UUID NOT NULL REFERENCES parties (id),
    invoice_ref               TEXT NOT NULL,
    amount_paise              BIGINT NOT NULL CHECK (amount_paise >= 0),
    due_date                  DATE NOT NULL,
    next_followup_at          TIMESTAMPTZ,
    attempt_count             INT NOT NULL DEFAULT 0,
    state                     TEXT NOT NULL DEFAULT 'DRAFT' CHECK (state IN (
        'DRAFT', 'ACTIVE', 'WAITING_OWNER_APPROVAL', 'DISPATCHED',
        'DISPUTED', 'PAID', 'SNOOZED')),
    last_continuum_source_id  TEXT,
    UNIQUE (tenant_id, invoice_ref)
);
CREATE INDEX obligations_due_idx ON obligations (next_followup_at)
    WHERE state IN ('ACTIVE', 'SNOOZED');
CREATE INDEX obligations_party_idx ON obligations (party_id);

CREATE TABLE proposed_adjustments (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    obligation_id         UUID NOT NULL REFERENCES obligations (id),
    adjustment_type       TEXT NOT NULL CHECK (adjustment_type IN (
        'DISCOUNT_CLAIM', 'PROMISE_DATE_CHANGE', 'UTR_PROOF')),
    proposed_amount_paise BIGINT CHECK (proposed_amount_paise >= 0),
    proposed_date         DATE,
    raw_evidence_text     TEXT NOT NULL,
    -- UNIQUE makes WhatsApp webhook retries idempotent.
    continuum_source_id   TEXT NOT NULL UNIQUE,
    status                TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at            TIMESTAMPTZ
);
CREATE INDEX adjustments_pending_idx ON proposed_adjustments (obligation_id)
    WHERE status = 'PENDING';

CREATE TABLE obligation_events (
    id                   BIGSERIAL PRIMARY KEY,
    obligation_id        UUID NOT NULL REFERENCES obligations (id),
    from_state           TEXT,
    to_state             TEXT NOT NULL,
    actor                TEXT NOT NULL CHECK (actor IN ('OWNER', 'DEBTOR', 'SYSTEM_CRON')),
    continuum_source_id  TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX obligation_events_idx ON obligation_events (obligation_id, created_at);

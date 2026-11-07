-- Transactional outbox: Continuum writes are queued in the same Postgres
-- transaction as the state change, then delivered with retries. A Continuum or
-- Sarvam outage can delay proof, but never loses a debtor claim.
CREATE TABLE continuum_outbox (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             TEXT NOT NULL,
    source_id           TEXT NOT NULL UNIQUE,
    text                TEXT NOT NULL,
    trusted             BOOLEAN NOT NULL,
    metadata            JSONB NOT NULL DEFAULT '{}',
    stamp_obligation_id UUID,
    status              TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'DONE', 'DEAD')),
    attempts            INT NOT NULL DEFAULT 0,
    next_attempt_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX continuum_outbox_due_idx ON continuum_outbox (next_attempt_at) WHERE status = 'PENDING';

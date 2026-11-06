-- What the shop owes suppliers (Section 43B(h) deadlines) and nightly Tally syncs from the owner's PC.

CREATE TABLE IF NOT EXISTS payables (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID NOT NULL REFERENCES tenants(id),
    supplier       TEXT NOT NULL,
    supplier_gstin TEXT,
    bill_ref       TEXT NOT NULL,
    bill_date      DATE NOT NULL,
    amount_paise   BIGINT NOT NULL CHECK (amount_paise >= 0),
    msme_class     TEXT NOT NULL DEFAULT 'unknown' CHECK (msme_class IN ('micro','small','other','unknown')),
    agreement_45   BOOLEAN NOT NULL DEFAULT false,   -- written agreement allows 45 days instead of 15
    paid_at        DATE,
    UNIQUE (tenant_id, bill_ref, supplier)
);
CREATE INDEX IF NOT EXISTS payables_open_idx ON payables (tenant_id, bill_date) WHERE paid_at IS NULL;

CREATE TABLE IF NOT EXISTS tally_syncs (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   UUID NOT NULL REFERENCES tenants(id),
    device_id   TEXT,
    receivables INT NOT NULL DEFAULT 0,
    payables    INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The agent may read payables of its own shop only, like the other ledger tables.
GRANT SELECT ON payables TO munim_agent_ro;
ALTER TABLE payables ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_tenant ON payables;
CREATE POLICY agent_tenant ON payables FOR SELECT TO munim_agent_ro
    USING (tenant_id = current_setting('munim.tenant_id', true)::uuid);

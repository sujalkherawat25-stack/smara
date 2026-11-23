-- Smara only starts messages nobody asked for (daily collection cards, the morning summary) for owners who turned them on.
-- Owners who already had invoices keep what they had.
CREATE TABLE proactive_optin (
    tenant_id  uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    enabled_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO proactive_optin (tenant_id) SELECT DISTINCT tenant_id FROM obligations ON CONFLICT DO NOTHING;

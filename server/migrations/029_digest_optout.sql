-- Owners who switched the weekly review and daily alerts off ('digest off'). No row = they get them.
CREATE TABLE IF NOT EXISTS digest_optout (
    tenant_id  uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now()
);

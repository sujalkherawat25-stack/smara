-- "Sign in" for Smara Desktop. The PC asks for a login, shows a short code, and the owner confirms it from their own
-- linked chat (one tap + YES). The PC then collects its device token and approvals token once. Only hashes are stored,
-- and the tokens themselves are never kept in this table.
CREATE TABLE IF NOT EXISTS desktop_logins (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code_hash    text NOT NULL UNIQUE,
    secret_hash  text NOT NULL UNIQUE,
    name         text NOT NULL,
    status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'asked', 'approved', 'done', 'denied')),
    tenant_id    uuid REFERENCES tenants(id) ON DELETE CASCADE,
    expires_at   timestamptz NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS desktop_logins_tenant ON desktop_logins (tenant_id) WHERE tenant_id IS NOT NULL;

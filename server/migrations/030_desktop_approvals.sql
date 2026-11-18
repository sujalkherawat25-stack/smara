-- Smara Desktop approvals: the owner answers PC approvals (browser steps, PC files, hand-offs) in Smara Desktop as well
-- as in chat. Authorised by an owner token minted from the owner's own chat ('desktop approvals'), never by the browser
-- helper's device token (that process drives a browser and must not approve its own steps). Only hashes are stored.
CREATE TABLE IF NOT EXISTS owner_desktop_pairings (
    code_hash    text PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    expires_at   timestamptz NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS owner_desktop_tokens (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name         text NOT NULL,
    token_hash   text NOT NULL UNIQUE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz,
    revoked_at   timestamptz
);
CREATE INDEX IF NOT EXISTS owner_desktop_tokens_tenant ON owner_desktop_tokens (tenant_id) WHERE revoked_at IS NULL;

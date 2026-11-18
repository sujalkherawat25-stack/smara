-- Optional: the owner lets Smara Desktop share what it learns with hosted Smara. Off until the owner turns it on, one row
-- per shop, revocable at any time. Notes from Desktop live under their own Continuum scope ({tenant}_desktop) so
-- revoking can erase exactly them. Credentials and vault secrets are never part of it.
CREATE TABLE IF NOT EXISTS owner_memory_sharing (
    tenant_id   uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    enabled     boolean NOT NULL DEFAULT false,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

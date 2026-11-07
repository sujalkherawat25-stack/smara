-- Encrypted per-shop secrets: one data key per shop, stored only wrapped by the server's master key.
CREATE TABLE IF NOT EXISTS tenant_keys (
    tenant_id      uuid PRIMARY KEY REFERENCES tenants(id),
    wrapped_key    bytea NOT NULL,
    nonce          bytea NOT NULL,
    master_version integer NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    rotated_at     timestamptz
);
CREATE TABLE IF NOT EXISTS tenant_secrets (
    tenant_id  uuid NOT NULL REFERENCES tenants(id),
    name       text NOT NULL,
    ciphertext bytea NOT NULL,
    nonce      bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

-- A customer who said STOP to a shop is never messaged by that shop again until they send START.
ALTER TABLE parties ADD COLUMN IF NOT EXISTS opted_out_at timestamptz;

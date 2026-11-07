-- Browser on the owner's own device (Smara Browser Bridge) and per-session site grants.

CREATE TABLE IF NOT EXISTS bridge_pairings (
    code_hash    text PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES tenants(id),
    expires_at   timestamptz NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bridge_devices (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants(id),
    name         text NOT NULL,
    token_hash   text NOT NULL UNIQUE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz,
    revoked_at   timestamptz
);
CREATE INDEX IF NOT EXISTS bridge_devices_tenant ON bridge_devices (tenant_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS bridge_jobs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants(id),
    device_id   uuid,
    session_id  text NOT NULL,
    command     jsonb NOT NULL,
    status      text NOT NULL DEFAULT 'PENDING',      -- PENDING | CLAIMED | DONE | EXPIRED
    result      jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    claimed_at  timestamptz,
    done_at     timestamptz
);
CREATE INDEX IF NOT EXISTS bridge_jobs_pending ON bridge_jobs (tenant_id, created_at) WHERE status = 'PENDING';

-- hosts the owner let the agent act on without asking for every click: [{"host": "...", "until": "ISO time"}]
ALTER TABLE agent_sessions ADD COLUMN IF NOT EXISTS grants jsonb NOT NULL DEFAULT '[]'::jsonb;

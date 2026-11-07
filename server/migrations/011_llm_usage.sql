-- Per-shop model usage, so each shop's cost is known before pricing it.
CREATE TABLE IF NOT EXISTS llm_usage (
    id                bigserial PRIMARY KEY,
    tenant_id         uuid NOT NULL REFERENCES tenants(id),
    session_id        uuid,
    model             text NOT NULL,
    prompt_tokens     integer NOT NULL DEFAULT 0,
    completion_tokens integer NOT NULL DEFAULT 0,
    total_tokens      integer NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS llm_usage_tenant_time ON llm_usage (tenant_id, created_at DESC);

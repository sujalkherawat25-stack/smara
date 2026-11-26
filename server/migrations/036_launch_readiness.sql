-- Extra allowance an admin can give one account (2x to 10x the normal daily limits, until a date), and when the Smara
-- team was last told that account was stopped by a limit.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS limit_times smallint NOT NULL DEFAULT 1;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS limit_until timestamptz;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS limit_alerted_at timestamptz;

-- Owners who asked for the Monday summary and alerts with 'digest on'. Before this, 'digest on' only removed an
-- opt-out, so in production (where Smara starts nothing unless asked) the promised summary never came.
CREATE TABLE IF NOT EXISTS digest_optin (
    tenant_id   uuid PRIMARY KEY REFERENCES tenants(id),
    created_at  timestamptz NOT NULL DEFAULT now()
);

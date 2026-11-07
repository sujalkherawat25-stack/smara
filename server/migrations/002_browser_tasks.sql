-- Approval state for browser side-effects (invariant 3). A 5th table, beyond the
-- 4 core ones, because a pending plan must survive restarts.
CREATE TABLE browser_tasks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL,
    instruction  TEXT NOT NULL,
    plan         JSONB NOT NULL,
    status       TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'DONE', 'FAILED')),
    result       JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at   TIMESTAMPTZ
);

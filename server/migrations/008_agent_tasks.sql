-- Work the agent schedules for itself (follow-ups) and sends the owner already
-- approved but that fall in quiet hours (deferred until the shop opens).
CREATE TABLE agent_tasks (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          UUID NOT NULL,
    run_at             TIMESTAMPTZ NOT NULL,
    kind               TEXT NOT NULL CHECK (kind IN ('followup', 'send_message')),
    payload            JSONB NOT NULL,
    status             TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'DONE', 'CANCELLED', 'FAILED')),
    created_by_session UUID,
    attempts           INT NOT NULL DEFAULT 0,
    last_error         TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at         TIMESTAMPTZ
);
CREATE INDEX agent_tasks_due_idx ON agent_tasks (run_at) WHERE status IN ('PENDING', 'RUNNING');
CREATE INDEX agent_tasks_tenant_idx ON agent_tasks (tenant_id, created_at);

-- Held owner notes keep their payload so they can be delivered later.
ALTER TABLE outbound_messages ADD COLUMN payload JSONB;

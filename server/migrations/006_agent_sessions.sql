-- Agent sessions: the full message history of one agent run, persisted so a
-- run can suspend at a gated tool call and resume after the owner decides.
CREATE TABLE agent_sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL,
    trust       TEXT NOT NULL CHECK (trust IN ('TRUSTED', 'UNTRUSTED', 'SYSTEM')),
    trigger     TEXT NOT NULL,                 -- owner_message | debtor_message | cron | ...
    party_id    UUID,                          -- set for debtor sessions: the only party they may touch
    status      TEXT NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'WAITING_APPROVAL', 'DONE', 'FAILED')),
    messages    JSONB NOT NULL DEFAULT '[]',
    pending     JSONB,                         -- the gated tool call awaiting the owner
    steps       INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX agent_sessions_waiting_idx ON agent_sessions (tenant_id) WHERE status = 'WAITING_APPROVAL';

-- Read-only role for the agent's query_ledger_sql tool. Row-level security
-- pins every query to one tenant (SET LOCAL munim.tenant_id), and the role can
-- only SELECT a few ledger tables: no tenants, outbox, sessions or secrets.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'munim_agent_ro') THEN
        CREATE ROLE munim_agent_ro NOLOGIN;
    END IF;
END $$;
GRANT munim_agent_ro TO CURRENT_USER;
GRANT USAGE ON SCHEMA public TO munim_agent_ro;
GRANT SELECT ON parties, obligations, proposed_adjustments, obligation_events TO munim_agent_ro;

ALTER TABLE parties ENABLE ROW LEVEL SECURITY;
ALTER TABLE obligations ENABLE ROW LEVEL SECURITY;
ALTER TABLE proposed_adjustments ENABLE ROW LEVEL SECURITY;
ALTER TABLE obligation_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY agent_tenant ON parties FOR SELECT TO munim_agent_ro
    USING (tenant_id = current_setting('munim.tenant_id', true)::uuid);
CREATE POLICY agent_tenant ON obligations FOR SELECT TO munim_agent_ro
    USING (tenant_id = current_setting('munim.tenant_id', true)::uuid);
CREATE POLICY agent_tenant ON proposed_adjustments FOR SELECT TO munim_agent_ro
    USING (EXISTS (SELECT 1 FROM obligations o WHERE o.id = obligation_id
                   AND o.tenant_id = current_setting('munim.tenant_id', true)::uuid));
CREATE POLICY agent_tenant ON obligation_events FOR SELECT TO munim_agent_ro
    USING (EXISTS (SELECT 1 FROM obligations o WHERE o.id = obligation_id
                   AND o.tenant_id = current_setting('munim.tenant_id', true)::uuid));

-- Session context the tools need, and a link from owner cards to the session they gate.
ALTER TABLE agent_sessions
    ADD COLUMN channel TEXT NOT NULL DEFAULT 'whatsapp',
    ADD COLUMN files TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE outbound_messages ADD COLUMN agent_session_id UUID;

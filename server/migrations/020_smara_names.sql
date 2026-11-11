-- The product is Smara, so the agent's read-only role and the tenant setting take the Smara names.
-- A role keeps its grants and policy memberships when it is renamed (they follow the role's id), so the rename is
-- one statement. The policies are then rewritten to read the smara.tenant_id setting that the app now sets.
-- Older databases still have munim_agent_ro; fresh ones have it from migration 006. Both end up as smara_agent_ro.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'munim_agent_ro')
       AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'smara_agent_ro') THEN
        ALTER ROLE munim_agent_ro RENAME TO smara_agent_ro;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'smara_agent_ro') THEN
        CREATE ROLE smara_agent_ro NOLOGIN;
    END IF;
END $$;
-- The same grants migration 006 and 016 gave the old role. Repeating them is harmless, and it covers a role that
-- already existed beside the old one.
GRANT USAGE ON SCHEMA public TO smara_agent_ro;
GRANT SELECT ON parties, obligations, proposed_adjustments, obligation_events, payables TO smara_agent_ro;
GRANT smara_agent_ro TO CURRENT_USER;

ALTER POLICY agent_tenant ON parties TO smara_agent_ro
    USING (tenant_id = current_setting('smara.tenant_id', true)::uuid);
ALTER POLICY agent_tenant ON obligations TO smara_agent_ro
    USING (tenant_id = current_setting('smara.tenant_id', true)::uuid);
ALTER POLICY agent_tenant ON proposed_adjustments TO smara_agent_ro
    USING (EXISTS (SELECT 1 FROM obligations o WHERE o.id = obligation_id
                   AND o.tenant_id = current_setting('smara.tenant_id', true)::uuid));
ALTER POLICY agent_tenant ON obligation_events TO smara_agent_ro
    USING (EXISTS (SELECT 1 FROM obligations o WHERE o.id = obligation_id
                   AND o.tenant_id = current_setting('smara.tenant_id', true)::uuid));
ALTER POLICY agent_tenant ON payables TO smara_agent_ro
    USING (tenant_id = current_setting('smara.tenant_id', true)::uuid);

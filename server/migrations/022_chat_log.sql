-- One searchable record of what the owner and Smara said, on every channel (Telegram, WhatsApp).
--
-- Until now the conversation lived only inside agent_sessions.messages: a new session started after 30 quiet minutes,
-- long sessions were compacted, and the prompt carried the last few exchanges clipped to ~200 characters. Anything
-- older was gone, so Smara "forgot". WhatsApp and Telegram cannot hand history back (neither API offers it), so Smara
-- keeps its own. Continuum stays the place for distilled, durable facts about customers and the shop.
CREATE TABLE IF NOT EXISTS chat_messages (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenants(id),
    channel     text NOT NULL,                                  -- telegram | whatsapp | wa_link
    role        text NOT NULL CHECK (role IN ('owner', 'smara')),
    kind        text NOT NULL DEFAULT 'text',                   -- text | voice | file | owner_text | owner_digest | owner_*_card ...
    content     text NOT NULL,
    session_id  uuid,                                           -- the agent run it belongs to, so a run never sees itself twice
    ref         text,                                           -- the channel's own message id (owner messages)
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_recent ON chat_messages (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS chat_messages_ref ON chat_messages (tenant_id, ref) WHERE ref IS NOT NULL;

-- Earlier conversations: what the owner typed and Smara answered (not tool calls or system notes), from the sessions
-- already stored. Per-message times were not kept, so they are spread over the session's own time span.
INSERT INTO chat_messages (tenant_id, channel, role, kind, content, session_id, created_at)
SELECT s.tenant_id, s.channel,
       CASE WHEN m.value->>'role' = 'user' THEN 'owner' ELSE 'smara' END,
       'text', left(m.value->>'content', 4000), s.id,
       least(s.updated_at, s.created_at + m.ordinality * interval '1 second')
  FROM agent_sessions s, jsonb_array_elements(s.messages) WITH ORDINALITY AS m(value, ordinality)
 WHERE s.trust = 'TRUSTED'
   AND m.value->>'role' IN ('user', 'assistant')
   AND jsonb_typeof(m.value->'content') = 'string'
   AND btrim(m.value->>'content') <> ''
   AND m.value->>'content' NOT LIKE '[system note]%'
   AND m.value->>'content' NOT LIKE '[Earlier conversation omitted%'
   AND CASE WHEN jsonb_typeof(m.value->'tool_calls') = 'array' THEN jsonb_array_length(m.value->'tool_calls') ELSE 0 END = 0;

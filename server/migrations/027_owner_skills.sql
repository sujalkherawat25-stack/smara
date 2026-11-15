-- A shop's own skills: workflows that worked, saved by the agent at the owner's wish (like Claude Code skills, but per shop).
-- The agent lists them in its instructions and loads one by name before a task of that kind. They never change what needs
-- the owner's approval. The owner can list and delete them in chat ('skills', 'skill delete <name>').
CREATE TABLE IF NOT EXISTS owner_skills (
    tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name         text NOT NULL CHECK (name ~ '^[a-z0-9-]{1,60}$'),
    description  text NOT NULL CHECK (char_length(description) BETWEEN 1 AND 300),
    body         text NOT NULL CHECK (char_length(body) <= 6000),
    version      integer NOT NULL DEFAULT 1,
    uses         integer NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    PRIMARY KEY (tenant_id, name)
);

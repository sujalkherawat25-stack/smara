-- Whole tasks the owner sends from WhatsApp/Telegram for their own PC to do (Smara Desktop's agent runs them locally).
CREATE TABLE pc_tasks (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    task         text NOT NULL,
    status       text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'waiting', 'done', 'failed', 'expired', 'cancelled')),
    goal_id      text,
    result       text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pc_tasks_queue ON pc_tasks (tenant_id, created_at) WHERE status = 'queued';

-- What the PC asks the owner while a task runs: an approval (Allow/Deny) or a one-time code typed in the chat.
CREATE TABLE pc_task_asks (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    task_id      uuid NOT NULL REFERENCES pc_tasks(id) ON DELETE CASCADE,
    request_id   text NOT NULL,
    kind         text NOT NULL DEFAULT 'approval' CHECK (kind IN ('approval', 'otp')),
    summary      text NOT NULL,
    answer       text,                 -- 'allow' | 'deny' | the code (deleted once the PC has it)
    answered_at  timestamptz,
    delivered_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, request_id)
);
CREATE INDEX pc_task_asks_open ON pc_task_asks (tenant_id) WHERE answered_at IS NULL;

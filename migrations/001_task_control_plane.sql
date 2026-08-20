-- Smara owns task state. Syntarus owns memory and is reached only by its API.
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(account_id, id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','running','waiting_approval','cancelling','completed','failed','cancelled')),
    requires_approval BOOLEAN NOT NULL DEFAULT TRUE,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS tasks_account_created ON tasks(account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS tasks_ready ON tasks(status, created_at) WHERE status='queued';

CREATE TABLE IF NOT EXISTS task_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(task_id, attempt)
);

CREATE TABLE IF NOT EXISTS task_steps (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    task_run_id TEXT NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    required_capability TEXT,
    executor_kind TEXT NOT NULL DEFAULT 'hosted',
    idempotency_key TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    retry_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(task_run_id, ordinal),
    UNIQUE(idempotency_key)
);

CREATE TABLE IF NOT EXISTS task_step_dependencies (
    step_id TEXT NOT NULL REFERENCES task_steps(id) ON DELETE CASCADE,
    depends_on_step_id TEXT NOT NULL REFERENCES task_steps(id) ON DELETE CASCADE,
    PRIMARY KEY(step_id, depends_on_step_id),
    CHECK(step_id <> depends_on_step_id)
);

CREATE TABLE IF NOT EXISTS task_events (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS task_events_task_created ON task_events(task_id, created_at);

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    task_step_id TEXT NOT NULL REFERENCES task_steps(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('pending','approved','denied','expired')),
    preview TEXT NOT NULL,
    decision_note TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    decided_at TIMESTAMPTZ
);

-- The first worker contract has task-level approvals. Later executor work can
-- add richer per-step requests through approval_requests without changing the
-- durable decision API used by current clients.
CREATE TABLE IF NOT EXISTS approvals (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('approved','denied')),
    note TEXT NOT NULL,
    decided_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    uri TEXT NOT NULL,
    sha256 TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- A record that an account was erased, kept after the erasure so it can be shown that it happened. It holds no personal
-- data: only a one-way hash of the shop id, when, and how much was removed.
CREATE TABLE IF NOT EXISTS erasure_log (
  id              bigserial PRIMARY KEY,
  tenant_hash     text NOT NULL,
  erased_at       timestamptz NOT NULL DEFAULT now(),
  continuum_users integer NOT NULL DEFAULT 0,
  files_removed   integer NOT NULL DEFAULT 0,
  rows_removed    integer NOT NULL DEFAULT 0
);

-- Importing a sheet looks each customer up by name (lower-cased) inside the shop. With no index that is a scan of the whole
-- parties table per invoice row, so a 190,000-row import ran for more than 30 minutes and held the database at 95% CPU.
CREATE INDEX IF NOT EXISTS parties_tenant_lname_idx ON parties (tenant_id, lower(canonical_name));

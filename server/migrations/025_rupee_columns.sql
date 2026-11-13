-- The agent writes SQL against the ledger and reads the rows back. Amounts are stored in paise, and the model sometimes divided
-- by 100 wrongly when it wrote the answer (22,000,000 paise came back as Rs 22,000 instead of Rs 2,20,000). These columns hold
-- the same amounts in rupees, computed by the database, so the model reads and sums rupees and never converts anything.
-- The paise columns stay: all the code that moves money uses them.
ALTER TABLE obligations ADD COLUMN IF NOT EXISTS amount_rupees NUMERIC
    GENERATED ALWAYS AS (ROUND(amount_paise::numeric / 100, 2)) STORED;
ALTER TABLE payables ADD COLUMN IF NOT EXISTS amount_rupees NUMERIC
    GENERATED ALWAYS AS (ROUND(amount_paise::numeric / 100, 2)) STORED;
ALTER TABLE proposed_adjustments ADD COLUMN IF NOT EXISTS proposed_amount_rupees NUMERIC
    GENERATED ALWAYS AS (ROUND(proposed_amount_paise::numeric / 100, 2)) STORED;

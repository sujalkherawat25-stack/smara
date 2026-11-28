-- Which remembered facts the owner set as a rule for Smara's work. The prompt lists these as rules to follow and the
-- rest of what it remembers as information (before this, whatever a memory search returned was labelled an order).
ALTER TABLE shop_facts ADD COLUMN IF NOT EXISTS is_rule boolean NOT NULL DEFAULT false;

-- Rows written before this column came only from the owner's own typed words (memory_pass). Those filed under a topic
-- that names a rule or a preference are rules; the prompt still tells the agent to keep to a plain instruction in the rest.
UPDATE shop_facts SET is_rule = true WHERE said <> '' AND topic ~ '(^|_)(rules?|preferences?)(_|$)';

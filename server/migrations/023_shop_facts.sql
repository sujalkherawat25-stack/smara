-- What the owner has told Smara, one current row per topic.
--
-- Continuum keeps every fact it is given and, whatever the wording, returns an old rule next to the new one that replaced
-- it (found by the recall evaluation: a rule changed from "before Friday" to "before Wednesday" was answered with Friday
-- half the time). So the standing facts the memory pass extracts also land here, keyed by topic: a new value for the same
-- topic replaces the old one, and the old wording is remembered in `superseded` so the copy Continuum still holds can be
-- left out of the prompt. Facts without a topic (a promise, a dispute) are additive and live only in Continuum.
CREATE TABLE IF NOT EXISTS shop_facts (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenants(id),
    slot        text NOT NULL,                 -- "<customer id or shop>:<topic>"; one row per slot
    party_id    uuid,                          -- the customer it is about; null for the shop as a whole
    topic       text NOT NULL,
    text        text NOT NULL,                 -- the current fact, one sentence
    said        text NOT NULL DEFAULT '',      -- the owner's own words for it
    superseded  text[] NOT NULL DEFAULT '{}',  -- earlier wordings as written to Continuum (fact plus the owner's words)
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, slot)
);
CREATE INDEX IF NOT EXISTS shop_facts_party ON shop_facts (tenant_id, party_id);

-- A customer who deals with several Smara shops on the shared number: their message waits here while we ask which shop
-- it is about, instead of being dropped.
CREATE TABLE IF NOT EXISTS customer_routing (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    phone       text NOT NULL,
    number_id   text NOT NULL DEFAULT '',
    original    jsonb NOT NULL,          -- the customer's message, to process once they pick a shop
    options     jsonb NOT NULL,          -- [{"tenant_id": ..., "name": ...}]
    created_at  timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    tenant_id   uuid                     -- the shop they picked
);
CREATE INDEX IF NOT EXISTS customer_routing_open ON customer_routing (phone) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS outbound_to_phone_time ON outbound_messages (to_phone, created_at DESC);

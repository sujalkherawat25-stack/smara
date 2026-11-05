-- Public Smara number: invite codes, the onboarding conversation, waitlist, usage limits, data requests.

CREATE TABLE IF NOT EXISTS invite_codes (
    code        text PRIMARY KEY,                 -- upper case, e.g. BSW-7Q2K
    label       text NOT NULL DEFAULT '',         -- who it was made for (a shop, a CA, a campaign)
    max_uses    integer NOT NULL DEFAULT 1,
    uses        integer NOT NULL DEFAULT 0,
    expires_at  timestamptz,
    revoked_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Where a new owner is in the sign-up chat.
CREATE TABLE IF NOT EXISTS onboarding_sessions (
    phone       text PRIMARY KEY,
    state       text NOT NULL,                    -- ASK_SHOP | ASK_UPI | DONE
    invite_code text,
    shop_name   text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- People who wrote without an invite: one row per phone, the owner of Smara reads this to decide who to invite.
CREATE TABLE IF NOT EXISTS waitlist (
    phone       text PRIMARY KEY,
    first_text  text NOT NULL DEFAULT '',
    last_text   text NOT NULL DEFAULT '',
    messages    integer NOT NULL DEFAULT 1,
    replied_at  timestamptz,
    invited_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Every message an owner sends, for daily limits (a runaway or abusive shop must not stall everyone on the shared model key).
CREATE TABLE IF NOT EXISTS owner_inbound_log (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenants(id),
    kind        text NOT NULL,                    -- text | file | audio | other
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS owner_inbound_recent ON owner_inbound_log (tenant_id, created_at DESC);

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS limits_exempt boolean NOT NULL DEFAULT false;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS joined_with_code text;
UPDATE tenants SET limits_exempt = true WHERE created_at < now();   -- shops that exist today are not limited

CREATE TABLE IF NOT EXISTS data_requests (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenants(id),
    kind        text NOT NULL,                    -- delete | export
    status      text NOT NULL DEFAULT 'OPEN',     -- OPEN | DONE
    created_at  timestamptz NOT NULL DEFAULT now(),
    done_at     timestamptz
);

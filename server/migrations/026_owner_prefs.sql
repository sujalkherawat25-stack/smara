-- How the owner wants Smara to answer them: the language, how long the answers are, and a short note of their own
-- ("amounts in lakhs", "call me Sujal bhai"). Set by the owner only (the 'style' command, its buttons, or by asking in chat),
-- read into the agent's instructions on every run. It changes the style of answers, never what needs the owner's approval.
CREATE TABLE IF NOT EXISTS owner_prefs (
    tenant_id   uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    language    text NOT NULL DEFAULT 'auto',
    length      text NOT NULL DEFAULT 'normal' CHECK (length IN ('short', 'normal', 'detailed')),
    note        text NOT NULL DEFAULT '' CHECK (char_length(note) <= 300),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

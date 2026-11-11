-- A new owner can now join from Telegram. The sign-up chat is keyed "tg:<chat id>" in onboarding_sessions.phone
-- (the phone is not known until the owner shares it), and the number Telegram confirms is kept here until the
-- shop is created. States for this path: ASK_PHONE | ASK_SHOP | ASK_UPI | DONE.
ALTER TABLE onboarding_sessions ADD COLUMN IF NOT EXISTS owner_phone text;

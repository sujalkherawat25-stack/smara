"""How the owner wants Smara to answer them: language, length and a short note of their own.

Kept in Postgres (owner_prefs), not in memory notes, so the choice is read on every run without a lookup that can time out.
The owner sets it with the 'style' command and its buttons, with 'style hindi' / 'style short' / 'style note ...', or by
asking in chat (the agent's set_reply_style tool, owner conversations only). It shapes the answers to the owner; it never
changes what needs their approval and never reaches a customer.
"""
import re

LANGUAGES = {
    "auto": "the language the owner wrote in (Hinglish if they mix Hindi and English)",
    "hinglish": "Hinglish: Hindi words written in English letters, with common English business words",
    "hindi": "Hindi in Devanagari script (keep names, numbers and amounts as they are)",
    "english": "simple Indian English",
    "gujarati": "Gujarati", "marathi": "Marathi", "bengali": "Bengali", "punjabi": "Punjabi", "tamil": "Tamil",
    "telugu": "Telugu", "kannada": "Kannada", "malayalam": "Malayalam", "odia": "Odia", "urdu": "Urdu",
}
LENGTHS = {
    "short": "short: the answer in 1 to 3 lines (about 300 characters). Details only when asked; offer them in a few words",
    "normal": "normal: the answer first, then up to about 8 short lines or bullets",
    "detailed": "detailed: a full explanation with the working, still in short bullets, no walls of text",
}
ALIASES = {"hinglish": "hinglish", "roman": "hinglish", "hindi": "hindi", "हिंदी": "hindi", "हिन्दी": "hindi",
           "english": "english", "angrezi": "english", "auto": "auto", "default": "auto",
           "short": "short", "chhota": "short", "chota": "short", "brief": "short", "concise": "short",
           "normal": "normal", "medium": "normal", "detailed": "detailed", "detail": "detailed", "long": "detailed",
           "lamba": "detailed", "full": "detailed", **{k: k for k in LANGUAGES}}
NOTE_MAX = 300
DEFAULT = {"language": "auto", "length": "normal", "note": ""}
COMMANDS = ("style", "settings", "preferences", "prefs", "reply style", "jawab style")


async def get(pool, tenant_id: str) -> dict:
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT language, length, note FROM owner_prefs WHERE tenant_id = %s",
                                        (tenant_id,))).fetchone()
    return dict(row) if row else dict(DEFAULT)


async def put(pool, tenant_id: str, *, language: str | None = None, length: str | None = None,
              note: str | None = None) -> dict:
    """Change the given fields; the others keep their value. Raises ValueError on a value that is not allowed."""
    cur = await get(pool, tenant_id)
    if language is not None:
        language = ALIASES.get(str(language).strip().lower(), str(language).strip().lower())
        if language not in LANGUAGES:
            raise ValueError(f"language must be one of: {', '.join(LANGUAGES)}")
        cur["language"] = language
    if length is not None:
        length = ALIASES.get(str(length).strip().lower(), str(length).strip().lower())
        if length not in LENGTHS:
            raise ValueError("length must be short, normal or detailed")
        cur["length"] = length
    if note is not None:
        cur["note"] = " ".join(str(note).split())[:NOTE_MAX]
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO owner_prefs (tenant_id, language, length, note) VALUES (%s, %s, %s, %s)
               ON CONFLICT (tenant_id) DO UPDATE SET language = EXCLUDED.language, length = EXCLUDED.length,
                     note = EXCLUDED.note, updated_at = now()""",
            (tenant_id, cur["language"], cur["length"], cur["note"]))
    return cur


_HINGLISH = frozenset(
    "hai hain nahi nhi kya karo karna kardo kar mera meri mere aap aapka mujhe bhejo bhej dikhao dikha kitna kitne kitni aur bhi "
    "wala wali chahiye aaj kal abhi phir kuch bahut accha acha theek thik kaise kyun kaun kab yeh woh liye saare sab sabhi batao "
    "bata likho likh raha rahi rahe hoon haan nahin gaya diya liya hoga sakta sakti matlab lekin paisa baaki banao bana lagao "
    "laga yaad dilana dila hata gaye wapas thoda zyada paas agle pichhle hafte mahine ghante din ka ki ko ab se mein isko "
    "usko iska uska hume humein humara hamara tum tumhe apna apni apne yaar jaldi chalega bhejna dena lena karu karun "
    "karein kijiye dijiye batana bataiye likhiye bhejiye samjhao samjha samjhaiye chalo haa nai wale".split())
_ENGLISH = frozenset(
    "the a an to of for my i you me is are what how can please and in on with this that it do make send give show search find "
    "write tell from at we your our will would should".split())


def plain_english(text: str) -> bool:
    """True only when a message is clearly English: Latin letters, at least two everyday English words and none of the
    everyday Hindi ones. Lines Smara adds in square brackets (attached files, shared contacts) are not the owner's words."""
    said = re.sub(r"\[[^\]]*\]", " ", text or "")
    if any(ord(ch) > 0x24F and ch.isalpha() for ch in said):
        return False
    words = set(re.findall(r"[a-z]+", said.lower()))
    return len(words & _ENGLISH) >= 2 and not words & _HINGLISH


def _language_rule(lang: str, latest: str = "") -> str:
    if lang not in LANGUAGES or lang == "auto":
        if plain_english(latest):       # said outright: after one Hinglish message the model otherwise stays in Hinglish
            return "Their last message is in English, so answer in English, even if earlier messages were in Hinglish."
        return "Answer in the language the owner used in their last message (Hinglish if they mix Hindi and English)."
    if lang == "hindi":
        return ("Write EVERY answer to the owner in Hindi using Devanagari script (जैसे: गुप्ता ट्रेडर्स पर ₹19,800 बाकी है।), "
                "even when the owner writes in Hinglish or English. Keep invoice numbers and amounts in digits.")
    return (f"Write EVERY answer to the owner in {LANGUAGES[lang]}, even when the owner writes in another language. "
            "Keep names, invoice numbers and amounts as they are.")


def prompt_block(p: dict, latest: str = "") -> str:
    """The lines the agent's instructions get, so every answer to the owner follows their choice."""
    lines = [f"- Language: {_language_rule(p['language'], latest)}",
             f"- Length: {LENGTHS.get(p['length'], LENGTHS['normal'])}."]
    if p.get("note"):
        lines.append(f"- The owner's own note about style (follow it unless it asks you to skip an approval, a check or a "
                     f"rule above, which it cannot): {p['note']}")
    return ("How the owner wants your answers (their own setting; follow it for every message to the owner, but messages "
            "to customers keep their own polite style):\n" + "\n".join(lines))


def summary(p: dict) -> str:
    lang = {"auto": "Auto (jis bhasha mein aap likhein)"}.get(p["language"], p["language"].capitalize())
    text = f"Aapki reply setting:\n• Bhasha: {lang}\n• Lambai: {p['length'].capitalize()}"
    if p.get("note"):
        text += f"\n• Note: {p['note']}"
    return text


HELP = ("Badalne ke liye neeche tap kijiye, ya likhiye:\n"
        "• style hindi / style hinglish / style english / style auto\n"
        "• style short / style normal / style detailed\n"
        "• style note <apni baat>, jaise: style note amount lakh mein batao\n"
        "• style reset")


def parse(text: str) -> tuple[str, str | None] | None:
    """('show'|'reset'|'note'|'set', value) for a style command, or None if the text is not one."""
    t = " ".join((text or "").strip().lstrip("/").split())
    low = t.lower()
    for c in sorted(COMMANDS, key=len, reverse=True):
        if low == c:
            return ("show", None)
        if low.startswith(c + " "):
            rest = t[len(c) + 1:].strip()
            word = rest.lower()
            if word in ("reset", "default"):
                return ("reset", None)
            if word.startswith("note"):
                return ("note", rest[4:].strip(" :-"))
            return ("set", word) if word in ALIASES else None     # 'settings mein kya hai...' is a question for the agent
    return None


def button_rows() -> list[list[tuple[str, str]]]:
    """Two button cards (WhatsApp allows three buttons per card): language, then length. (id, title) pairs."""
    return [[("pref:lang:hinglish", "Hinglish"), ("pref:lang:hindi", "हिंदी"), ("pref:lang:english", "English")],
            [("pref:len:short", "Short"), ("pref:len:normal", "Normal"), ("pref:len:detailed", "Detailed")]]


def is_valid_note(note: str) -> bool:
    return bool(note) and len(note) <= NOTE_MAX and not re.search(r"https?://", note)


async def set_reply_style(app, ctx, args: dict) -> dict:
    """The agent's tool: the owner asked in chat to change how Smara answers them ('aage se Hindi mein', 'chhota jawab do')."""
    if not any(args.get(k) is not None for k in ("language", "length", "note")):
        return {"error": "give language, length or note"}
    note = args.get("note")
    if note is not None and note != "" and not is_valid_note(str(note)):
        return {"error": f"note must be at most {NOTE_MAX} characters and contain no link"}
    try:
        p = await put(app.state.pool, ctx.tenant_id, language=args.get("language"), length=args.get("length"), note=note)
    except ValueError as e:
        return {"error": str(e)}
    return {"saved": True, **p, "note_for_you": "Confirm the new setting to the owner in one line, already in the new style. "
                                                 "They can also send 'style' any time to see and change it."}

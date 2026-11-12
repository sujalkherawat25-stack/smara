"""A second recall scenario, written after the memory fixes and not tuned against: new shop, new facts, new question styles.

recall_scenario.py (v1) is the scenario the memory code was developed against, so its scores are optimistic. This one was
written afterwards, with different customers, different facts and harder cases, and the memory code has not been changed to
fit it. Same shape as v1 (history(), PROBES, CUSTOMERS, FACTS, READY_QUERY) and the same scorer.

What is harder here:
  - a rule changed THREE times (the topic has to be reused twice), with the owner naming the customer only by a short name
  - a shop-wide rule and a customer's exception to it, which must not replace each other
  - money owed in both directions, asked as yes/no questions so a flipped direction shows
  - a figure corrected a minute later ("galat bola")
  - facts about people (who keeps the keys, who delivers), a tone preference, a bank and the days it is visited
  - questions about things never said but close to things that were (a person who was mentioned, a bank that was named)
"""
import random

from evals.recall_scenario import Probe, Turn, normalise, score  # noqa: F401  (the types and the scorer are shared with v1)

CUSTOMERS = [("Jain Medical", "919876510001", 2_700_000), ("Patel Hardware", "919876510002", 1_800_000),
             ("Rathore Agencies", "919876510003", 640_000), ("Khan Brothers", "919876510004", 8_000_000),
             ("Deshmukh & Sons", "919876510005", 1_250_000), ("Singh Traders", "919876510006", 410_000)]

READY_QUERY = "Hamari bank Axis hai Monday aur Thursday ko jaate hain"

FACTS = [
    Turn(66, "telegram", "Hamari bank Axis hai, aur hum bank sirf Monday aur Thursday ko jaate hain.",
         "Note kar liya: Axis bank, Monday aur Thursday.", "bank_days"),
    Turn(62, "telegram", "GST return hum 20 tareekh ko file karte hain, CA ka office Jaipur mein hai, naam Mehta saab.",
         "Theek hai: GST 20 tareekh ko, CA Mehta saab, Jaipur.", "gst_ca"),
    Turn(55, "whatsapp", "Jain Medical purana customer hai, unko 60 din ka credit milta hai.",
         "Jain Medical: 60 din ka credit. Note kar liya.", "jain_credit"),
    Turn(48, "telegram", "Patel Hardware ko 2% discount dena hai agar wo 10 din ke andar pay kare.",
         "Patel Hardware: 2% discount agar 10 din ke andar pay karein.", "patel_discount"),
    Turn(44, "telegram", "Customers ko message naram tone mein bhejna, hamesha 'aap' bolna, 'tum' kabhi nahi.",
         "Samajh gaya: naram tone, 'aap' hi.", "tone"),
    Turn(40, "whatsapp", "Rathore Agencies ko reminder sirf Monday ko bhejna.",
         "Done: Rathore Agencies ko sirf Monday ko reminder.", "rathore_v1"),
    Turn(35, "whatsapp", "Reminder hamesha dopahar 12 baje ke baad hi bhejna, usse pehle nahi.",
         "Theek hai, reminders 12 baje ke baad hi jayenge.", "hours_shop"),
    Turn(30, "telegram", "Khan Brothers ne mujhe 80,000 dene hain, 20 tareekh ko.",
         "Note: Khan Brothers aapko Rs 80,000 denge, 20 tareekh ko.", "khan_owes_me"),
    Turn(27, "whatsapp", "Delivery ka kaam Pappu dekhta hai, aur godown ki chaabi Suresh ke paas rehti hai.",
         "Note: delivery Pappu, godown ki chaabi Suresh ke paas.", "people"),
    Turn(21, "telegram", "Rathore Agencies ka rule badal diya: ab sirf Wednesday ko bhejna.",
         "Updated: Rathore Agencies ko ab sirf Wednesday ko.", "rathore_v2"),
    Turn(18, "telegram", "Deshmukh & Sons ka credit limit 1,00,000 hai.",
         "Deshmukh & Sons ka credit limit Rs 1,00,000.", "deshmukh_v1"),
    Turn(17.99, "telegram", "Nahi nahi, galat bola - Deshmukh ka credit limit 1,50,000 hai.",
         "Theek kiya: Deshmukh & Sons ka credit limit Rs 1,50,000.", "deshmukh_v2"),
    Turn(14, "telegram", "Singh Traders ko reminder subah 10 baje bhi bhej sakte ho, unke liye exception hai.",
         "Samajh gaya: Singh Traders ko 10 baje ke baad bhi bhej sakta hoon.", "singh_exception"),
    Turn(9, "whatsapp", "Mujhe Singh Traders ko 32,500 dene hain, 5 tareekh tak.",
         "Note: aapko Singh Traders ko Rs 32,500 dene hain, 5 tareekh tak.", "i_owe_singh"),
    Turn(5, "whatsapp", "Rathore ka reminder day phir badal do - ab Friday ko bhejna, Wednesday nahi.",
         "Updated: Rathore Agencies ko ab Friday ko.", "rathore_v3"),
    Turn(3, "telegram", "Parso Jain Medical ka cheque clear hona hai, 27,000 ka, HDFC ka.",
         "Note: Jain Medical, cheque Rs 27,000, HDFC, parso.", "jain_cheque"),
    Turn(0.08, "whatsapp", "Abhi Patel Hardware ka phone aaya, bole kal shaam 5 baje tak 18,000 bhej denge.",
         "Note: Patel Hardware kal shaam 5 baje tak Rs 18,000 bhejenge.", "patel_today"),
]

_OWNER = ["Jain Medical ka kitna baaki hai?", "Aaj ka status batao", "digest bhejo", "Overdue kitne hain?", "Patel Hardware ka bill dikhao",
          "Kal kya due hai?", "Statement bana do", "Sabse bada overdue kaun hai?", "Rathore Agencies ka pending kitna hai?",
          "Is hafte kitna collection hua?", "Deshmukh ke bills dikhao", "total outstanding?", "good morning",
          "Khan Brothers ka invoice check karo", "supplier payments kya bache hain?", "thanks", "ok", "report bhejo",
          "Singh Traders ka last bill kab ka hai?"]
_SMARA = ["Jain Medical ka Rs 9,200 baaki hai.", "Kuch invoices open hain, kuch overdue.", "Digest bhej diya.",
          "Kuch invoices overdue hain.", "Patel Hardware ka ek bill bada hai.", "Kal koi invoice due nahi hai.",
          "Statement PDF bhej diya.", "Sabse bada overdue Khan Brothers ka hai.", "Rathore Agencies ka Rs 6,400 baaki hai.",
          "Is hafte Rs 14,000 aaya.", "Deshmukh & Sons ke bills dikha diye.", "Total outstanding dikha diya.", "Good morning!",
          "Khan Brothers ka ek invoice open hai.", "Koi supplier payment pending nahi dikh rahi.", "Welcome!", "Theek hai.",
          "Report bhej di.", "Singh Traders ka last bill purana hai."]


def chatter(n_recent: int = 70, n_old: int = 55, seed: int = 11) -> list[Turn]:
    """Ordinary back and forth, dense in the last week, thin before. Never states a planted fact or a probe token."""
    rng = random.Random(seed)
    turns = []
    for i in range(n_recent + n_old):
        days = rng.uniform(0.25, 7.0) if i < n_recent else rng.uniform(7.0, 69.0)
        k = rng.randrange(len(_OWNER))
        turns.append(Turn(round(days, 3), rng.choice(("telegram", "whatsapp")), _OWNER[k], _SMARA[k]))
    return turns


def history() -> list[Turn]:
    return sorted(FACTS + chatter(), key=lambda t: -t.days_ago)


def _age(fact: str) -> float:
    return next(t.days_ago for t in FACTS if t.fact == fact)


PROBES = [
    Probe("bank_days", "old", "Hamari bank kaun si hai aur hum kis din jaate hain?", "telegram", (r"axis", r"monday|somvar", r"thursday|guruvar|brihaspat"),
          fact="bank_days", age_days=_age("bank_days")),
    Probe("gst_ca", "old", "GST return kab file karte hain aur humara CA kaun hai?", "whatsapp", (r"20 tareekh|20 tarikh|20th|bees", r"mehta"),
          fact="gst_ca", age_days=_age("gst_ca")),
    Probe("jain_credit", "verbatim", "Jain Medical ko kitne din ka credit milta hai?", "telegram", (r"\b60 (din|days)|60-day|saath din",),
          fact="jain_credit", age_days=_age("jain_credit")),
    Probe("patel_discount", "verbatim", "Patel Hardware ko kitna discount dena hai aur kab tak pay kare to?", "whatsapp",
          (r"2\s?%|\b2 percent|do percent|2 pratishat", r"\b10 din|10 days|das din"), fact="patel_discount", age_days=_age("patel_discount")),
    Probe("tone", "rule", "Customers ko message kis tone mein bhejna hai?", "telegram", (r"naram|soft|polite|gentle|vinamr", r"\baap\b"),
          fact="tone", age_days=_age("tone")),
    Probe("rathore_day", "update", "Rathore Agencies ko reminder kaun se din bhejna hai?", "telegram", (r"friday|shukra",),
          stale=r"monday|somvar|wednesday|budh", fact="rathore_v3", age_days=_age("rathore_v3")),
    Probe("hours_shop", "rule", "Reminders kitne baje ke baad bhejte hain?", "whatsapp", (r"\b12 baje|12 pm|12:00|dopahar|noon",),
          fact="hours_shop", age_days=_age("hours_shop")),
    Probe("singh_hours", "exception", "Singh Traders ko reminder kitne baje bhej sakte hain?", "telegram", (r"\b10 baje|10 am|10:00|das baje",),
          fact="singh_exception", age_days=_age("singh_exception")),
    Probe("khan_direction", "direction", "Khan Brothers ko mujhe kuch dena hai kya?", "telegram", (r"\bnahi\b|\bno\b|ulta|opposite", r"80000"),
          fact="khan_owes_me", age_days=_age("khan_owes_me")),
    Probe("singh_direction", "direction", "Singh Traders mujhe paisa dega kya?", "whatsapp", (r"\bnahi\b|\bno\b|ulta|opposite", r"32500"),
          fact="i_owe_singh", age_days=_age("i_owe_singh")),
    Probe("deshmukh_limit", "update", "Deshmukh & Sons ka credit limit kitna hai?", "telegram", (r"150000",),
          stale=r"100000", fact="deshmukh_v2", age_days=_age("deshmukh_v2")),
    Probe("keys", "verbatim", "Godown ki chaabi kiske paas rehti hai?", "telegram", (r"suresh",), fact="people", age_days=_age("people")),
    Probe("delivery", "verbatim", "Delivery ka kaam kaun dekhta hai?", "whatsapp", (r"pappu",), fact="people", age_days=_age("people")),
    Probe("jain_cheque", "cross_channel", "Jain Medical ka cheque kitne ka hai aur kis bank ka?", "whatsapp", (r"27000", r"hdfc"),
          fact="jain_cheque", age_days=_age("jain_cheque")),
    Probe("patel_today", "same_day", "Patel Hardware ne abhi kya bola tha?", "telegram", (r"\b5 baje|5 pm|paanch|shaam", r"18000"),
          fact="patel_today", age_days=_age("patel_today")),
    Probe("u_patel_gstin", "unknown", "Patel Hardware ka GSTIN kya hai?", "telegram", unknown=True,
          forbid=(r"\b\d{2}(?!x{5})[a-z]{5}\d{4}[a-z]\d[a-z\d][a-z\d]\b",)),
    Probe("u_suresh_phone", "unknown", "Suresh ka phone number kya hai?", "whatsapp", unknown=True, forbid=(r"\b\d{10}\b",)),
    Probe("u_singh_meeting", "unknown", "Singh Traders ke saath last meeting kab hui thi?", "telegram", unknown=True),
    Probe("u_axis_account", "unknown", "Axis bank mein humara account number kya hai?", "whatsapp", unknown=True, forbid=(r"\b\d{9,}\b",)),
]

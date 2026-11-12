"""A synthetic owner history with planted facts, and questions that test whether Smara remembers them.

Everything here is invented (names, numbers, cheques). The history spans 70 days on Telegram and WhatsApp: a few dozen
facts the owner states once, buried in ordinary chatter that gets denser towards today, so the recent window alone cannot
hold them. Each probe is a question asked later in a fresh conversation, with how to tell a right answer from a wrong one:
  - `expect`: groups of regexes; every group must match the reply (after numbers are normalised: no commas, no Rs sign);
  - `forbid`: tokens that would show a mix-up with a look-alike fact (another customer's cheque number);
  - `stale`: the old value of a rule that was changed; seen without the new value it is a stale answer;
  - `unknown`: the owner never said this; the right answer is that Smara does not know.
No model is needed to import or check this file (tests/test_recall_eval.py does).
"""
import random
import re
from dataclasses import dataclass, field

CUSTOMERS = [("Ravi Kirana", "919876500001", 1_250_000), ("Sharma Store", "919876500002", 2_000_000),
             ("Gupta Traders", "919876500003", 4_500_000), ("Anand Stores", "919876500004", 840_000),
             ("Bhatia & Co", "919876500005", 1_525_050), ("Mishra Brothers", "919876500006", 310_000)]


@dataclass(frozen=True)
class Turn:
    days_ago: float
    channel: str            # telegram | whatsapp
    owner: str
    smara: str
    fact: str = ""          # the planted fact this turn states ("" for chatter)


@dataclass(frozen=True)
class Probe:
    id: str
    kind: str               # verbatim | rule | update | cross_channel | same_day | old | decoy | unknown
    question: str
    channel: str
    expect: tuple = ()      # tuple of regex alternations, all must match
    forbid: tuple = ()
    stale: str = ""
    unknown: bool = False
    fact: str = ""          # id of the planted fact it tests
    age_days: float = 0.0


FACTS = [
    Turn(70, "telegram", "Humare GST filing ke liye CA Mr. Verma hain, unka office Udaipur mein hai.",
         "Note kar liya: CA Mr. Verma, Udaipur.", "ca"),
    Turn(33, "whatsapp", "Ravi kirana hamesha mahine ke last week mein payment karta hai, uspe pressure mat dalna.",
         "Theek hai, Ravi ko mahine ke last week se pehle nahi dabaunga.", "ravi_habit"),
    Turn(28, "telegram", "Sharma ji ko Friday se pehle reminder mat bhejna.",
         "Samajh gaya, Sharma ji ko Friday se pehle reminder nahi jayega.", "sharma_rule_old"),
    Turn(25, "telegram", "Humara godown Plot 14, Industrial Area, Banswara mein hai.",
         "Godown ka address save kar liya: Plot 14, Industrial Area, Banswara.", "godown"),
    Turn(20, "whatsapp", "Reminder hamesha subah 11 baje ke baad hi bhejna, usse pehle nahi.",
         "Done, reminders 11 baje ke baad hi jayenge.", "reminder_time"),
    Turn(17, "whatsapp", "Mishra Brothers ka credit limit 50,000 rakho, usse upar maal mat dena.",
         "Mishra Brothers ka credit limit Rs 50,000 note kar liya.", "mishra_limit"),
    Turn(12, "whatsapp", "Gupta Traders ne bola 45,000 ka cheque Tuesday ko aayega, cheque number 004512 hai.",
         "Note: Gupta Traders, Rs 45,000, cheque 004512, Tuesday.", "gupta_cheque"),
    Turn(10, "telegram", "Kumar Steel ko 1,85,000 rupaye 15 tareekh tak dene hain.",
         "Kumar Steel: Rs 1,85,000, 15 tareekh tak. Yaad rakhunga.", "kumar_due"),
    Turn(8, "telegram", "Mere munshi ka naam Dinesh hai, Tally wahi dekhta hai.",
         "Theek hai, Tally Dinesh dekhta hai.", "munshi"),
    Turn(6, "whatsapp", "Bhatia ka cheque number 008830 hai, 12,000 ka.",
         "Note: Bhatia & Co, cheque 008830, Rs 12,000.", "bhatia_cheque"),
    Turn(4, "telegram", "Sharma ji ke liye rule badal do: ab Wednesday se pehle reminder mat bhejna.",
         "Updated: Sharma ji ko ab Wednesday se pehle reminder nahi jayega.", "sharma_rule_new"),
    Turn(2, "telegram", "Anand Stores ne dispute kiya hai, bole 3 boxes damaged aaye the.",
         "Anand Stores dispute: 3 boxes damaged. Note kar liya.", "anand_dispute"),
    Turn(0.125, "whatsapp", "Aaj Bhatia ne bola kal subah 10 baje 15,000 bhejega.",
         "Note: Bhatia kal subah 10 baje Rs 15,000 bhejega.", "bhatia_promise"),
]

_OWNER = ["Ravi ka kitna baaki hai?", "Aaj ka status batao", "digest bhejo", "Overdue kitne hain?", "Sharma Store ka bill dikhao",
          "Kal kya due hai?", "Statement bana do", "Sabse bada overdue kaun hai?", "Mishra ka kitna pending hai?",
          "Is hafte kitna collection hua?", "Anand Stores ko kitna dena hai unko?", "total outstanding?", "good morning",
          "Gupta Traders ka invoice check karo", "supplier payments kya bache hain?", "thanks", "ok", "report bhejo"]
_SMARA = ["Ravi Kirana ka Rs 12,500 baaki hai.", "3 open invoices, 2 overdue, Rs 1,85,750 outstanding.", "Digest bhej diya.",
          "2 invoices overdue hain.", "Sharma Store ka ek bill Rs 20,000 ka hai.", "Kal koi invoice due nahi hai.",
          "Statement PDF bhej diya.", "Sabse bada overdue Gupta Traders ka hai.", "Mishra Brothers ka Rs 3,100 baaki hai.",
          "Is hafte Rs 14,000 aaya.", "Anand Stores ka Rs 8,400 baaki hai.", "Total Rs 1,85,750.", "Good morning!",
          "Gupta Traders ka ek invoice open hai.", "Koi supplier payment pending nahi dikh rahi.", "Welcome!", "Theek hai.",
          "Report bhej di."]


def chatter(n_recent: int = 70, n_old: int = 50, seed: int = 7) -> list[Turn]:
    """Ordinary back and forth: dense in the last week, thin before. Never states a planted fact or probe token."""
    rng = random.Random(seed)
    turns = []
    for i in range(n_recent + n_old):
        days = rng.uniform(0.25, 7.0) if i < n_recent else rng.uniform(7.0, 69.0)
        k = rng.randrange(len(_OWNER))
        turns.append(Turn(round(days, 3), rng.choice(("telegram", "whatsapp")), _OWNER[k], _SMARA[k]))
    return turns


def history() -> list[Turn]:
    """Facts and chatter, oldest first."""
    return sorted(FACTS + chatter(), key=lambda t: -t.days_ago)


def _age(fact: str) -> float:
    return next(t.days_ago for t in FACTS if t.fact == fact)


UNKNOWN = re.compile(r"(nahi|nahin|not (found|mentioned|available|sure|have|know)|no (record|information|info|idea)|don.?t|do not|"
                     r"cannot|can.?t|unable|unknown|bataya nahi|zikr|koi .*nahi)", re.I)

PROBES = [
    Probe("ca", "old", "Humara CA kaun hai?", "whatsapp", (r"verma",), fact="ca", age_days=_age("ca")),
    Probe("ravi_habit", "verbatim", "Ravi kirana usually kab tak payment karta hai?", "telegram",
          (r"last week|mahine ke last|month.?end|end of (the )?month|aakhri|ant mein",), fact="ravi_habit", age_days=_age("ravi_habit")),
    Probe("godown", "verbatim", "Humara godown kahan hai?", "telegram", (r"plot 14",), fact="godown", age_days=_age("godown")),
    Probe("reminder_time", "rule", "Reminders ko kitne baje ke baad bhejna hai?", "telegram", (r"\b11\b",),
          fact="reminder_time", age_days=_age("reminder_time")),
    Probe("mishra_limit", "verbatim", "Mishra Brothers ka credit limit kitna rakha tha?", "telegram", (r"50000",),
          fact="mishra_limit", age_days=_age("mishra_limit")),
    Probe("gupta_cheque_no", "decoy", "Gupta Traders ka cheque number kya tha?", "telegram", (r"004512",), forbid=(r"008830",),
          fact="gupta_cheque", age_days=_age("gupta_cheque")),
    Probe("gupta_cheque_amt", "cross_channel", "Gupta Traders ka cheque kitne ka aane wala tha aur kab?", "telegram",
          (r"45000", r"tuesday|mangal"), fact="gupta_cheque", age_days=_age("gupta_cheque")),
    Probe("kumar_due", "verbatim", "Kumar Steel ko kitna dena hai aur kab tak?", "whatsapp", (r"185000", r"\b15\b"),
          fact="kumar_due", age_days=_age("kumar_due")),
    Probe("munshi", "verbatim", "Tally kaun dekhta hai?", "telegram", (r"dinesh",), fact="munshi", age_days=_age("munshi")),
    Probe("sharma_rule_now", "update", "Sharma ji ko reminder kab se bhej sakte hain?", "telegram", (r"wednesday|budh",),
          stale=r"friday|shukra", fact="sharma_rule_new", age_days=_age("sharma_rule_new")),
    Probe("anand_dispute", "cross_channel", "Anand Stores ka kya dispute chal raha tha?", "whatsapp",
          (r"damag|kharab|toot|tut", r"\b3\b|teen|three"), fact="anand_dispute", age_days=_age("anand_dispute")),
    Probe("bhatia_promise", "same_day", "Bhatia ne payment kab bhejne ka bola tha aur kitna?", "telegram",
          (r"\b10\b", r"15000"), fact="bhatia_promise", age_days=_age("bhatia_promise")),
    Probe("gstin", "unknown", "Mera GSTIN kya hai jo invoices par jata hai?", "telegram", unknown=True,
          forbid=(r"\b\d{2}[a-z]{5}\d{4}[a-z]\d[a-z\d][a-z\d]\b",)),
    Probe("salary", "unknown", "Dinesh ki salary kitni hai?", "whatsapp", unknown=True),
    Probe("verma_phone", "unknown", "Verma ji ka phone number kya hai?", "telegram", unknown=True, forbid=(r"\b\d{10}\b",)),
]


def normalise(text: str) -> str:
    t = re.sub(r"<[^>]+>", " ", text.lower())
    t = re.sub(r"(?<=\d),(?=\d)", "", t)
    t = t.replace("₹", " ").replace("rs.", " ").replace("rs ", " ")
    return " ".join(t.split())


def score(probe: Probe, reply: str) -> dict:
    """{'ok', 'why'}: why is '' for a pass, else what was wrong (missing, confused, stale, invented)."""
    t = normalise(reply)
    if not t:
        return {"ok": False, "why": "no reply"}
    if any(re.search(f, t) for f in probe.forbid):
        return {"ok": False, "why": "confused with another fact" if not probe.unknown else "invented an answer"}
    if probe.unknown:
        return {"ok": bool(UNKNOWN.search(t)), "why": "" if UNKNOWN.search(t) else "did not say it does not know"}
    if probe.stale:
        # A changed rule: the answer must not conclude with the old value. Saying "it used to be X" is fine, so phrases
        # that frame X as the past are removed before looking for X.
        old = probe.stale
        framed = (r"(pehle|earlier|previously|purana|purani|before that|was|tha|thi)\W+(?:\w+\W+){0,4}?(?:" + old + r")"
                  r"|(?:" + old + r")\W+(?:\w+\W+){0,3}?(?:tha|thi|was|purana|purani|old)")
        if re.search(old, re.sub(framed, " ", t)):
            return {"ok": False, "why": "stale answer"}
    missing = [e for e in probe.expect if not re.search(e, t)]
    if missing:
        return {"ok": False, "why": "missing: " + " + ".join(missing)}
    return {"ok": True, "why": ""}

"""A third recall scenario, written before the money-statement fix was released and not tuned against.

v1 was developed against; v2's statements were then used to diagnose why the memory step lost money statements, so v2 can no
longer judge that fix. This one has a different shop and different facts, and it is built to be clean where v2 was not:

  - suppliers the owner OWES are not in the ledger at all, so "do they owe me?" cannot be answered from the ledger by mistake
  - customers who owe the owner are in the ledger with the same amount the owner states, so nothing contradicts
  - a payable that is later paid off, asked as "do I still owe anything?"
  - a rule changed once with the customer's day changed in different words, a discount the shop RECEIVES, a bounced cheque,
    a renewal date, a person and her day off, and a statement made three hours ago
  - never-said questions that are close to said ones (a supplier's phone number, a policy number, a salary)

Same shape as v1 and v2 (history(), PROBES, CUSTOMERS, FACTS, READY_QUERY) and the same scorer.
"""
import random

from evals.recall_scenario import Probe, Turn, normalise, score  # noqa: F401  (the types and the scorer are shared)

# customers only: each owes the shop what the shop's owner says it owes. Suppliers (Gadre, Hegde, Eknath) are NOT here.
CUSTOMERS = [("Bhatt Mart", "919876520001", 960_000), ("Chopra Cycles", "919876520002", 4_100_000),
             ("Dubey Sweets", "919876520003", 530_000), ("Fernandes Bakery", "919876520004", 1_500_000),
             ("Gill Motors", "919876520005", 2_200_000), ("Iyer Tiffins", "919876520006", 380_000)]

READY_QUERY = "Hamara office Saturday ko band rehta hai"

FACTS = [
    Turn(62, "telegram", "Humari insurance renewal 10 January ko hai, LIC wali.",
         "Note: LIC insurance renewal 10 January ko.", "insurance"),
    Turn(58, "telegram", "Bhatt Mart ke saath humne 90 din ka credit tay kiya hai.",
         "Bhatt Mart: 90 din ka credit. Note kar liya.", "bhatt_credit"),
    Turn(52, "whatsapp", "Mujhe Gadre Packaging ko 64,000 dene hain, 18 tareekh tak.",
         "Note: aapko Gadre Packaging ko Rs 64,000 dene hain, 18 tareekh tak.", "i_owe_gadre"),
    Turn(47, "telegram", "Chopra Cycles ne mujhe 41,000 dene hain, 25 tareekh ko, cheque se.",
         "Note: Chopra Cycles aapko Rs 41,000 denge, 25 tareekh ko, cheque se.", "chopra_owes_me"),
    Turn(44, "telegram", "Hamara office Saturday ko band rehta hai.",
         "Theek hai: office Saturday ko band.", "office_off"),
    Turn(40, "whatsapp", "Dubey Sweets ko reminder sirf Tuesday ko bhejna.",
         "Done: Dubey Sweets ko sirf Tuesday ko reminder.", "dubey_v1"),
    Turn(36, "telegram", "Mujhe Hegde Steel ko 20,000 dene hain.",
         "Note: aapko Hegde Steel ko Rs 20,000 dene hain.", "i_owe_hegde"),
    Turn(33, "telegram", "Eknath Traders hume 2% discount dete hain agar hum 7 din ke andar pay karein.",
         "Note: Eknath Traders 2% discount dete hain, 7 din ke andar payment par.", "eknath_discount"),
    Turn(28, "whatsapp", "Fernandes Bakery ka 15,000 ka cheque bounce ho gaya tha, dobara cheque lena hai.",
         "Note: Fernandes Bakery ka Rs 15,000 ka cheque bounce hua, naya cheque lena hai.", "fernandes_bounce"),
    Turn(20, "telegram", "Accounts ka kaam Pooja dekhti hai, uski chhutti Sunday ko hoti hai.",
         "Note: accounts Pooja dekhti hai, Sunday ko chhutti.", "accounts_pooja"),
    Turn(12, "telegram", "Dubey Sweets ke liye din badal do, unko ab Thursday ko bhejna, Tuesday nahi.",
         "Updated: Dubey Sweets ko ab Thursday ko reminder.", "dubey_v2"),
    Turn(10, "whatsapp", "Hegde Steel ko 20,000 de diye maine, ab kuch baaki nahi.",
         "Note: Hegde Steel ka Rs 20,000 chuka diya, kuch baaki nahi.", "paid_hegde"),
    Turn(0.12, "whatsapp", "Abhi Bhatt Mart ne bola kal 25,000 ka cheque bhej rahe hain.",
         "Note: Bhatt Mart kal Rs 25,000 ka cheque bhejenge.", "bhatt_today"),
]

_OWNER = ["Chopra Cycles ka kitna baaki hai?", "Aaj ka status batao", "digest bhejo", "Overdue kitne hain?", "Gill Motors ka bill dikhao",
          "Kal kya due hai?", "Statement bana do", "Sabse bada overdue kaun hai?", "Iyer Tiffins ka pending kitna hai?",
          "Is hafte kitna collection hua?", "Fernandes Bakery ke bills dikhao", "total outstanding?", "good morning",
          "Dubey Sweets ka invoice check karo", "supplier payments kya bache hain?", "thanks", "ok", "report bhejo",
          "Bhatt Mart ka last bill kab ka hai?"]
_SMARA = ["Chopra Cycles ka ek invoice open hai.", "Kuch invoices open hain, kuch overdue.", "Digest bhej diya.",
          "Kuch invoices overdue hain.", "Gill Motors ka ek bill bada hai.", "Kal koi invoice due nahi hai.",
          "Statement PDF bhej diya.", "Sabse bada overdue Chopra Cycles ka hai.", "Iyer Tiffins ka Rs 3,800 baaki hai.",
          "Is hafte Rs 14,000 aaya.", "Fernandes Bakery ke bills dikha diye.", "Total outstanding dikha diya.", "Good morning!",
          "Dubey Sweets ka ek invoice open hai.", "Koi supplier payment pending nahi dikh rahi.", "Welcome!", "Theek hai.",
          "Report bhej di.", "Bhatt Mart ka last bill purana hai."]


def chatter(n_recent: int = 70, n_old: int = 55, seed: int = 23) -> list[Turn]:
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
    Probe("insurance", "old", "Insurance renewal kab hai?", "telegram", (r"10 jan|january|10th jan",), fact="insurance", age_days=_age("insurance")),
    Probe("bhatt_credit", "verbatim", "Bhatt Mart ko kitne din ka credit diya hai?", "whatsapp", (r"\b90 (din|days)|90-day|nabbe",),
          fact="bhatt_credit", age_days=_age("bhatt_credit")),
    Probe("gadre_amount", "payable", "Gadre Packaging ko humein kitna dena hai aur kab tak?", "telegram", (r"64000", r"\b18 tareekh|18 tarikh|18th|atharah"),
          fact="i_owe_gadre", age_days=_age("i_owe_gadre")),
    Probe("gadre_direction", "direction", "Gadre Packaging humein paisa dega kya?", "whatsapp", (r"\bnahi\b|\bno\b|ulta|opposite", r"64000"),
          fact="i_owe_gadre", age_days=_age("i_owe_gadre")),
    Probe("chopra_amount", "receivable", "Chopra Cycles se kitna aana hai aur kaise?", "telegram", (r"41000", r"cheque"),
          fact="chopra_owes_me", age_days=_age("chopra_owes_me")),
    Probe("office_off", "verbatim", "Hamara office kis din band rehta hai?", "whatsapp", (r"saturday|shanivar|shaniwar",),
          fact="office_off", age_days=_age("office_off")),
    Probe("dubey_day", "update", "Dubey Sweets ko reminder kaun se din bhejna hai?", "telegram", (r"thursday|guruvar|brihaspat",),
          stale=r"tuesday|mangal", fact="dubey_v2", age_days=_age("dubey_v2")),
    Probe("hegde_paid", "paid_off", "Hegde Steel ko abhi kuch dena hai kya?", "telegram", (r"\bnahi\b|\bno\b|de diye|chuka|paid|clear|settled",),
          forbid=(r"20000[^.]{0,40}(dene hain|dena hai|baaki hai|pending|due)",), fact="paid_hegde", age_days=_age("paid_hegde")),
    Probe("eknath_discount", "received_terms", "Eknath Traders se humein kya discount milta hai aur kab?", "whatsapp",
          (r"2\s?%|\b2 percent|do percent", r"\b7 din|7 days|saat din"), fact="eknath_discount", age_days=_age("eknath_discount")),
    Probe("fernandes_bounce", "verbatim", "Fernandes Bakery ke cheque ka kya hua tha?", "telegram", (r"bounce|wapas|return|dishonou?r", r"15000"),
          fact="fernandes_bounce", age_days=_age("fernandes_bounce")),
    Probe("accounts", "verbatim", "Accounts ka kaam kaun dekhta hai aur uski chhutti kab hoti hai?", "whatsapp", (r"pooja", r"sunday|ravivar|itwar"),
          fact="accounts_pooja", age_days=_age("accounts_pooja")),
    Probe("bhatt_today", "same_day", "Bhatt Mart ne abhi kya bola tha?", "telegram", (r"25000", r"\bkal\b|tomorrow|cheque"),
          fact="bhatt_today", age_days=_age("bhatt_today")),
    Probe("u_gadre_phone", "unknown", "Gadre Packaging ka phone number kya hai?", "telegram", unknown=True, forbid=(r"\b\d{10}\b",)),
    Probe("u_bhatt_gstin", "unknown", "Bhatt Mart ka GSTIN kya hai?", "whatsapp", unknown=True,
          forbid=(r"\b\d{2}(?!x{5})[a-z]{5}\d{4}[a-z]\d[a-z\d][a-z\d]\b",)),
    Probe("u_pooja_salary", "unknown", "Pooja ki salary kitni hai?", "telegram", unknown=True),
    Probe("u_lic_policy", "unknown", "LIC policy number kya hai?", "whatsapp", unknown=True, forbid=(r"\b\d{8,}\b",)),
]

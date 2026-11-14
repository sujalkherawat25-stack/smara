"""The owner's own bookkeeping by chat: a sale on credit (udhaar) and money received.

Both are tools the agent calls when the OWNER says so. Nothing is written until the owner taps Approve on the card, and the
card shows exactly what will change. The arithmetic is done here, never by the model:

  record_sale     a new invoice for a customer: amount, credit days, an invoice number (made up if the owner gave none).
  record_payment  money received from a customer. It goes to the oldest open invoice first, or to the one the owner names.
  set_customer_phone  the WhatsApp number of customers already in the ledger (no card: see its docstring).

`plan_*` checks the request against the ledger and returns what would change (or an error for the model). `apply_*` makes the
change inside one transaction and re-checks it there, so a ledger that changed after the card was shown is refused, not
overwritten. A customer's own message can never reach these tools (policy).
"""
import re
import uuid
from datetime import datetime, time, timedelta, timezone

from src import clock
from src.bridge.continuum import scope_user
from src.bridge.outbox import enqueue
from src.agent import names
from src.loop.upi import rupees

MAX_RUPEES = 100_000_000          # Rs 10 crore: an extra zero typed by mistake is refused, not recorded
DEFAULT_CREDIT_DAYS = 30
MAX_CREDIT_DAYS = 365
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,39}")
SOURCE = "CHAT"                   # obligations.source for an invoice the owner recorded in chat


class LedgerChanged(Exception):
    """The ledger is not what it was when the owner was shown the card."""


def _like(text: str) -> str:
    return "%" + re.sub(r"([\\%_])", r"\\\1", text) + "%"


def _amount(args: dict):
    """(paise, error). Rupees as the owner said them; at most two decimals."""
    try:
        r = float(args.get("rupees"))
    except (TypeError, ValueError):
        return None, "rupees must be a number"
    if not (0 < r <= MAX_RUPEES) or abs(round(r * 100) - r * 100) > 1e-6:      # also refuses nan and infinity
        return None, f"rupees must be more than 0 and at most {MAX_RUPEES:,} (two decimals at most)"
    return round(r * 100), None


async def _customer(conn, tenant_id: str, name) -> tuple[dict | None, str | None]:
    """One customer by exact name or alias, else by a name that contains the words. (row, error); (None, None) = none found."""
    name = " ".join(str(name or "").split())
    if not name or len(name) > 120:
        return None, "customer must be a name of 1 to 120 characters"
    exact = await (await conn.execute(
        """SELECT id, canonical_name FROM parties WHERE tenant_id = %s AND (lower(canonical_name) = lower(%s)
              OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE lower(a) = lower(%s))) ORDER BY canonical_name LIMIT 6""",
        (tenant_id, name, name))).fetchall()
    rows = exact or await (await conn.execute(
        "SELECT id, canonical_name FROM parties WHERE tenant_id = %s AND canonical_name ILIKE %s ESCAPE '\\' ORDER BY canonical_name LIMIT 6",
        (tenant_id, _like(name)))).fetchall()
    if not rows:                                            # a spelling or hearing variant: Agrawal for Agarwal, Devanagari
        allp = await (await conn.execute("SELECT id, canonical_name FROM parties WHERE tenant_id = %s LIMIT 5000",
                                         (tenant_id,))).fetchall()
        close = {n for n, _ in names.best(name, [p["canonical_name"] for p in allp])}
        rows = [p for p in allp if p["canonical_name"] in close][:6]
    if len(rows) > 1:
        return None, "several customers match: " + "; ".join(r["canonical_name"] for r in rows) + ". Ask the owner which one."
    return (rows[0], None) if rows else (None, None)


async def find_customer(app, ctx, args: dict) -> dict:
    """Customers whose name is close to what the owner said or the voice note was heard as. Reads only."""
    name = " ".join(str(args.get("name") or "").split())
    if not name or len(name) > 120:
        return {"error": "name must be 1 to 120 characters"}
    async with app.state.pool.connection() as conn:
        rows = await (await conn.execute("SELECT canonical_name, aliases FROM parties WHERE tenant_id = %s LIMIT 5000",
                                         (ctx.tenant_id,))).fetchall()
    scored = []
    for r in rows:
        s = max([names.score(name, r["canonical_name"])] + [names.score(name, a) for a in (r["aliases"] or [])])
        if s >= 0.75:
            scored.append((r["canonical_name"], round(s, 2)))
    scored.sort(key=lambda t: (-t[1], t[0]))
    out = [{"customer": n, "match": s} for n, s in scored[:6]]
    if not out:
        return {"found": 0, "note": "no customer in the ledger has a name like that; say so, do not guess"}
    return {"found": len(out), "customers": out,
            "note": "Use the customer's name exactly as listed. If more than one is listed and the best is not clearly ahead, ask the owner which."}


async def _free_ref(conn, tenant_id: str, wanted) -> tuple[str | None, str | None]:
    wanted = str(wanted or "").strip()
    if wanted:
        if not _REF.fullmatch(wanted):
            return None, "invoice_ref may use letters, digits and . / _ - only, up to 40 characters"
        taken = await (await conn.execute("SELECT 1 FROM obligations WHERE tenant_id = %s AND invoice_ref = %s",
                                          (tenant_id, wanted))).fetchone()
        return (None, f"invoice {wanted} already exists for this shop") if taken else (wanted, None)
    stem = f"S-{clock.today():%y%m%d}"
    for n in range(1, 1000):
        ref = f"{stem}-{n}"
        if not await (await conn.execute("SELECT 1 FROM obligations WHERE tenant_id = %s AND invoice_ref = %s",
                                         (tenant_id, ref))).fetchone():
            return ref, None
    return None, "could not make an invoice number; give invoice_ref"


async def plan_sale(pool, tenant_id: str, args: dict) -> tuple[dict | None, str | None]:
    paise, err = _amount(args)
    if err:
        return None, err
    try:
        days = DEFAULT_CREDIT_DAYS if args.get("credit_days") in (None, "") else int(args["credit_days"])
    except (TypeError, ValueError):
        return None, "credit_days must be a whole number"
    if not 0 <= days <= MAX_CREDIT_DAYS:
        return None, f"credit_days must be between 0 and {MAX_CREDIT_DAYS}"
    async with pool.connection() as conn:
        party, err = await _customer(conn, tenant_id, args.get("customer"))
        if err:
            return None, err
        ref, err = await _free_ref(conn, tenant_id, args.get("invoice_ref"))
        if err:
            return None, err
    name = party["canonical_name"] if party else " ".join(str(args["customer"]).split())
    return {"kind": "sale", "customer": name, "party_id": str(party["id"]) if party else None, "new_customer": party is None,
            "amount_paise": paise, "credit_days": days, "due_date": (clock.today() + timedelta(days=days)).isoformat(),
            "invoice_ref": ref, "note": str(args.get("note") or "")[:300]}, None


async def plan_payment(pool, tenant_id: str, args: dict) -> tuple[dict | None, str | None]:
    paise, err = _amount(args)
    if err:
        return None, err
    async with pool.connection() as conn:
        party, err = await _customer(conn, tenant_id, args.get("customer"))
        if err:
            return None, err
        if not party:
            return None, f"no customer named {str(args.get('customer'))[:60]!r} in the ledger; ask the owner who paid"
        open_ = await (await conn.execute(
            """SELECT id, invoice_ref, amount_paise, due_date FROM obligations
                WHERE tenant_id = %s AND party_id = %s AND state NOT IN ('PAID', 'DRAFT')
                ORDER BY due_date, invoice_ref""", (tenant_id, str(party["id"])))).fetchall()
    want = str(args.get("invoice_ref") or "").strip()
    if want:
        open_ = [o for o in open_ if o["invoice_ref"].lower() == want.lower()]
        if not open_:
            return None, f"{party['canonical_name']} has no open invoice {want}"
    if not open_:
        return None, f"{party['canonical_name']} has no open invoices"
    owed = sum(o["amount_paise"] for o in open_)
    if paise > owed:
        return None, (f"{party['canonical_name']} owes only {rupees(owed)}{' on ' + want if want else ''}, "
                      f"less than {rupees(paise)}. Ask the owner to check the amount.")
    left, split = paise, []
    for o in open_:                                  # oldest due date first
        if left <= 0:
            break
        pay = min(left, o["amount_paise"])
        split.append({"obligation_id": str(o["id"]), "invoice_ref": o["invoice_ref"], "due_date": o["due_date"].isoformat(),
                      "before_paise": o["amount_paise"], "pay_paise": pay, "after_paise": o["amount_paise"] - pay})
        left -= pay
    return {"kind": "payment", "customer": party["canonical_name"], "party_id": str(party["id"]), "amount_paise": paise,
            "split": split, "still_owed_paise": owed - paise, "note": str(args.get("note") or "")[:300]}, None


def _day(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d %b %Y").lstrip("0")


def card_text(plan: dict) -> str:
    """What the owner reads before tapping Approve."""
    if plan["kind"] == "sale":
        new = " (new customer, no WhatsApp number yet)" if plan["new_customer"] else ""
        return (f"Add this sale to your ledger?\n\n{plan['customer']}{new} owes {rupees(plan['amount_paise'])}\n"
                f"Invoice {plan['invoice_ref']}, due {_day(plan['due_date'])} ({plan['credit_days']} days)\n\n"
                "Nothing is sent to the customer.")
    lines = [f"• {s['invoice_ref']}: " + ("paid in full" if s["after_paise"] == 0 else f"{rupees(s['after_paise'])} left")
             for s in plan["split"]]
    return (f"Record {rupees(plan['amount_paise'])} received from {plan['customer']}?\n\n" + "\n".join(lines) +
            f"\n\n{plan['customer']} will still owe {rupees(plan['still_owed_paise'])}.\nNothing is sent to the customer.")


def _stamp(plan: dict) -> dict:
    return {"actor": "OWNER", "via": "chat", "kind": plan["kind"]}


async def apply_sale(pool, tenant_id: str, plan: dict) -> dict:
    if plan.get("kind") != "sale" or not isinstance(plan.get("amount_paise"), int) or plan["amount_paise"] <= 0:
        return {"error": "that request is not valid any more; ask me again"}
    due = datetime.fromisoformat(plan["due_date"]).date()
    followup = max(datetime.combine(due, time.min, tzinfo=timezone.utc), datetime.now(timezone.utc))
    async with pool.connection() as conn:
        async with conn.transaction():
            if plan["party_id"]:
                party = await (await conn.execute("SELECT id FROM parties WHERE id = %s AND tenant_id = %s FOR SHARE",
                                                  (plan["party_id"], tenant_id))).fetchone()
                if not party:
                    return {"error": "that customer is no longer in the ledger; ask me again"}
                party_id = str(party["id"])
            else:
                party_id = str((await (await conn.execute(
                    "INSERT INTO parties (tenant_id, canonical_name, whatsapp_phone) VALUES (%s, %s, NULL) RETURNING id",
                    (tenant_id, plan["customer"]))).fetchone())["id"])
            row = await (await conn.execute(
                """INSERT INTO obligations (tenant_id, party_id, invoice_ref, amount_paise, due_date, next_followup_at, state, source)
                   VALUES (%s, %s, %s, %s, %s, %s, 'ACTIVE', %s)
                   ON CONFLICT (tenant_id, invoice_ref) DO NOTHING RETURNING id""",
                (tenant_id, party_id, plan["invoice_ref"], plan["amount_paise"], due, followup, SOURCE))).fetchone()
            if row is None:
                raise LedgerChanged(f"invoice {plan['invoice_ref']} was taken in the meantime")
            await conn.execute("INSERT INTO obligation_events (obligation_id, from_state, to_state, actor) VALUES (%s, NULL, 'ACTIVE', 'OWNER')",
                               (row["id"],))
            await enqueue(conn, user_id=scope_user(tenant_id, party_id), source_id=f"book_{uuid.uuid4().hex}", trusted=True,
                          text=(f"Owner recorded a sale on credit to {plan['customer']}: {rupees(plan['amount_paise'])}, invoice "
                                f"{plan['invoice_ref']}, due {plan['due_date']}. {plan['note']}").strip(),
                          stamp_obligation_id=str(row["id"]), metadata=_stamp(plan))
    return {"recorded": True, "invoice_ref": plan["invoice_ref"], "customer": plan["customer"],
            "amount_rupees": plan["amount_paise"] / 100, "due_date": plan["due_date"], "new_customer": plan["new_customer"]}


async def apply_payment(pool, tenant_id: str, plan: dict) -> dict:
    if plan.get("kind") != "payment" or not plan.get("split"):
        return {"error": "that request is not valid any more; ask me again"}
    done = []
    async with pool.connection() as conn:
        async with conn.transaction():
            for s in plan["split"]:
                cur = await (await conn.execute(
                    "SELECT amount_paise, state FROM obligations WHERE id = %s AND tenant_id = %s AND party_id = %s FOR UPDATE",
                    (s["obligation_id"], tenant_id, plan["party_id"]))).fetchone()
                if not cur or cur["state"] in ("PAID", "DRAFT") or cur["amount_paise"] != s["before_paise"]:
                    raise LedgerChanged(f"invoice {s['invoice_ref']} changed after the card was shown")
                to_state = "PAID" if s["after_paise"] == 0 else cur["state"]
                if s["after_paise"] == 0:
                    await conn.execute("UPDATE obligations SET state = 'PAID', next_followup_at = NULL WHERE id = %s", (s["obligation_id"],))
                else:
                    await conn.execute("UPDATE obligations SET amount_paise = %s WHERE id = %s", (s["after_paise"], s["obligation_id"]))
                await conn.execute("INSERT INTO obligation_events (obligation_id, from_state, to_state, actor) VALUES (%s, %s, %s, 'OWNER')",
                                   (s["obligation_id"], cur["state"], to_state))
                await enqueue(conn, user_id=scope_user(tenant_id, plan["party_id"]), source_id=f"book_{uuid.uuid4().hex}", trusted=True,
                              text=(f"Owner recorded {rupees(s['pay_paise'])} received from {plan['customer']} on invoice {s['invoice_ref']} "
                                    f"({'paid in full' if s['after_paise'] == 0 else rupees(s['after_paise']) + ' left'}). {plan['note']}").strip(),
                              stamp_obligation_id=s["obligation_id"], metadata=_stamp(plan))
                done.append({"invoice_ref": s["invoice_ref"], "paid_rupees": s["pay_paise"] / 100, "left_rupees": s["after_paise"] / 100})
    return {"recorded": True, "customer": plan["customer"], "received_rupees": plan["amount_paise"] / 100, "invoices": done,
            "still_owed_rupees": plan["still_owed_paise"] / 100}


# ---- customers' phone numbers --------------------------------------------------------------------------------------------
MAX_PHONE_ENTRIES = 50


CONFIRM_TTL = timedelta(minutes=15)
_pending: dict[tuple, tuple[datetime, int | None]] = {}      # (tenant, customer id, phone) -> (when shown, owner message id)


def _phone(raw) -> str:
    """A WhatsApp number in the importer's own form (91 + 10 digits), or '' if it is not one. An Indian mobile has 10 digits
    starting 6-9 (after an optional 0 or 91). Another country's number is accepted only with an explicit +."""
    text = str(raw or "").strip().removesuffix(".0")
    digits = re.sub(r"\D", "", text)
    if text.startswith("+") and not digits.startswith("91"):
        return digits if 8 <= len(digits) <= 15 and digits[0] != "0" else ""
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return "91" + digits if re.fullmatch(r"[6-9]\d{9}", digits) else ""


def _spoken(phone: str) -> str:
    """The number as digits in groups, for reading back."""
    d = phone[2:] if phone.startswith("91") and len(phone) == 12 else phone
    return " ".join([d[:5], d[5:]]) if len(d) == 10 else d


_DIGIT_WORDS = {
    **{w: "0" for w in ("zero", "shunya", "sifar", "शून्य", "ज़ीरो", "जीरो", "सिफ़र")},
    **{w: "1" for w in ("one", "ek", "एक")}, **{w: "2" for w in ("two", "do", "दो")},
    **{w: "3" for w in ("three", "teen", "तीन")}, **{w: "4" for w in ("four", "char", "chaar", "चार")},
    **{w: "5" for w in ("five", "paanch", "panch", "पांच", "पाँच")}, **{w: "6" for w in ("six", "chhe", "chhah", "छह", "छः", "छै")},
    **{w: "7" for w in ("seven", "saat", "सात")}, **{w: "8" for w in ("eight", "aath", "आठ")},
    **{w: "9" for w in ("nine", "nau", "नौ")},
}
_REPEAT = {"double": 2, "डबल": 2, "triple": 3, "ट्रिपल": 3}


def heard_digits(text: str) -> tuple[str, list[str]]:
    """The digits in what speech-to-text heard, in order: written digits (also Devanagari ०-९), spoken digit words in Hindi
    or English, and 'double 4' / 'डबल चार'. Returns (all digits joined, the separate runs of digits said one after another)."""
    text = str(text or "").translate(str.maketrans("०१२३४५६७८९", "0123456789")).lower()
    runs, cur, repeat = [], "", 1
    for tok in re.findall(r"[0-9]+|[^\s\d.,;:!?।()\"'+\-/]+", text):     # words by spaces: Hindi vowel signs are not \w
        if tok in _REPEAT:
            repeat = _REPEAT[tok]
            continue
        d = tok if tok.isdigit() else _DIGIT_WORDS.get(tok)
        if d is None:
            if cur:
                runs.append(cur)
            cur, repeat = "", 1
            continue
        cur += d * repeat if len(d) == 1 else d
        repeat = 1
    if cur:
        runs.append(cur)
    return "".join(runs), runs


async def _recent_voice_text(conn, tenant_id: str) -> str:
    rows = await (await conn.execute(
        "SELECT content FROM chat_messages WHERE tenant_id = %s AND role = 'owner' AND kind = 'voice' "
        "AND created_at > now() - interval '15 minutes' ORDER BY id DESC LIMIT 3", (tenant_id,))).fetchall()
    return "\n".join(r["content"] for r in rows)


async def _last_owner_message(conn, tenant_id: str) -> tuple[int | None, str | None]:
    row = await (await conn.execute("SELECT id, kind FROM chat_messages WHERE tenant_id = %s AND role = 'owner' "
                                    "ORDER BY id DESC LIMIT 1", (tenant_id,))).fetchone()
    return (row["id"], row["kind"]) if row else (None, None)


async def set_customer_phone(app, ctx, args: dict) -> dict:
    """Set the WhatsApp number of customers already in the ledger, as the owner typed it in chat. Runs without a card: the owner
    gave the number themselves, the answer shows what was saved, and every reminder to that number still waits for their tap.
    Several customers can be given at once. A number that belongs to another customer, or is the owner's own, is refused."""
    entries = args.get("entries")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_PHONE_ENTRIES:
        return {"error": f"entries must be a list of 1 to {MAX_PHONE_ENTRIES} items, each with a customer and a phone"}
    from src import tenants
    tenant = await tenants.by_id(app.state.pool, ctx.tenant_id)
    own = re.sub(r"\D", "", tenant.owner_phone or "")
    results, saved = [], 0
    async with app.state.pool.connection() as conn:
        msg_id, kind = await _last_owner_message(conn, ctx.tenant_id)
        heard = kind == "voice"
        for e in entries:
            r = {"customer": str(e.get("customer") if isinstance(e, dict) else e)[:80]}
            results.append(r)
            if not isinstance(e, dict):
                r["error"] = "each entry needs a customer and a phone"
                continue
            phone = _phone(e.get("phone"))
            if not phone:
                r["error"] = ("not a valid Indian mobile number (10 digits starting 6, 7, 8 or 9). If it came from a voice note it was "
                              "probably heard wrong: ask the owner to send it again or type it")
                continue
            if own and phone == own:
                r["error"] = "that is the owner's own number, not the customer's"
                continue
            party, err = await _customer(conn, ctx.tenant_id, e.get("customer"))
            if err or not party:
                r["error"] = err or "no customer by that name in the ledger"
                continue
            if heard:                      # a number heard in a voice note is read back first; saved only after the owner's next message
                key, now = (ctx.tenant_id, str(party["id"]), phone), datetime.now(timezone.utc)
                p = _pending.get(key)
                if not (args.get("confirmed") is True and p and now - p[0] < CONFIRM_TTL and p[1] != msg_id):
                    national = phone[2:] if phone.startswith("91") and len(phone) == 12 else phone
                    joined, runs = heard_digits(await _recent_voice_text(conn, ctx.tenant_id))
                    if national not in joined and not any(national in run for run in runs):
                        r["error"] = (f"{_spoken(phone)} is not the number in the voice note: the digits heard were "
                                      f"{' '.join(runs) or 'none'}. Never add, drop or change a digit. Tell the owner exactly "
                                      "what was heard and ask them to say the number again slowly, or type it")
                        continue
                    _pending[key] = (now, msg_id)
                    r.update({"customer": party["canonical_name"], "needs_confirmation": True, "heard_number": _spoken(phone),
                              "_party_id": str(party["id"]), "_phone": phone})
                    continue
            async with conn.transaction():
                other = await (await conn.execute(
                    "SELECT canonical_name FROM parties WHERE tenant_id = %s AND whatsapp_phone = %s AND id <> %s",
                    (ctx.tenant_id, phone, party["id"]))).fetchone()
                if other:
                    r["error"] = f"{phone} already belongs to {other['canonical_name']}"
                    continue
                old = (await (await conn.execute("SELECT whatsapp_phone FROM parties WHERE id = %s AND tenant_id = %s FOR UPDATE",
                                                 (party["id"], ctx.tenant_id))).fetchone())["whatsapp_phone"]
                await conn.execute("UPDATE parties SET whatsapp_phone = %s WHERE id = %s AND tenant_id = %s",
                                   (phone, party["id"], ctx.tenant_id))
            _pending.pop((ctx.tenant_id, str(party["id"]), phone), None)
            r.update({"customer": party["canonical_name"], "phone": phone})
            if old and old != phone:
                r["replaced_number_ending"] = old[-4:]
            saved += 1
    out = {"saved": saved, "results": results,
           "note": "Reminders to these customers still wait for the owner's approval; the owner sees the number on each card."}
    heard_rows = [r for r in results if r.get("needs_confirmation")]
    if heard_rows:
        sent = await _confirm_card(app, ctx, tenant, heard_rows)
        out["note"] = (
            "The number came from a voice note, so nothing is saved yet for the entries marked needs_confirmation. "
            + ("Smara has sent the owner a card that shows the exact digits with 'Haan, save karo' and 'Nahi' buttons; their tap "
               "saves it. Do NOT write the number again yourself; say in one short line that you have asked them to check it. "
               if sent else
               "Read each heard_number back to the owner exactly as given here, digit for digit, and ask them to confirm. ")
            + "If they confirm by message instead, call set_customer_phone again with the same entries and confirmed: true. "
              "If they give a different number, use that one.")
    return out


PHONE_CARDS: dict[str, tuple[datetime, str, list[dict]]] = {}     # token -> (when, tenant, [{party_id, customer, phone}])


async def _confirm_card(app, ctx, tenant, rows: list[dict]) -> bool:
    """The read-back of a number heard in a voice note, written by Smara (not the model), with Yes/No buttons."""
    import secrets
    from src.loop.owner_channel import send_to_owner
    token = secrets.token_urlsafe(8)
    items = [{"party_id": r["_party_id"], "customer": r["customer"], "phone": r.pop("_phone")} for r in rows]
    for r in rows:
        r.pop("_party_id", None)
    lines = "\n".join(f"• {i['customer']}: *{_spoken(i['phone'])}*" for i in items)
    body = (f"Voice note mein ye number suna:\n{lines}\n\nSahi hai to save kar dun? Galat ho to 'Nahi' dabakar number "
            "dobara boliye ya type kijiye.")
    card = {"messaging_product": "whatsapp", "to": tenant.owner_phone, "type": "interactive", "interactive": {
        "type": "button", "body": {"text": body[:1000]}, "action": {"buttons": [
            {"type": "reply", "reply": {"id": f"phone_ok:{token}", "title": "Haan, save karo"}},
            {"type": "reply", "reply": {"id": f"phone_no:{token}", "title": "Nahi"}}]}}}
    PHONE_CARDS[token] = (datetime.now(timezone.utc), ctx.tenant_id, items)
    try:
        await send_to_owner(app, tenant, card, kind="owner_text")
    except Exception:
        PHONE_CARDS.pop(token, None)
        return False
    return True


async def decide_phone_card(app, tenant_id: str, token: str, ok: bool) -> str:
    """The owner tapped the confirmation card of a heard number."""
    entry = PHONE_CARDS.pop(token, None)
    if not entry or entry[1] != tenant_id or datetime.now(timezone.utc) - entry[0] > CONFIRM_TTL:
        return "Ye card purana ho gaya. Number dobara bhejiye."
    if not ok:
        return "Theek hai, save nahi kiya. Number dobara boliye (dheere) ya type kar dijiye."
    from src import tenants
    tenant = await tenants.by_id(app.state.pool, tenant_id)
    own = re.sub(r"\D", "", tenant.owner_phone or "")
    done = []
    async with app.state.pool.connection() as conn:
        for i in entry[2]:
            if own and i["phone"] == own:
                done.append(f"• {i['customer']}: ye aapka apna number hai, save nahi kiya")
                continue
            err = await _save_phone(conn, tenant_id, i["party_id"], i["phone"])
            done.append(f"• {i['customer']}: {err}" if err else f"• {i['customer']}: {_spoken(i['phone'])} save ho gaya")
    return "\n".join(done)


async def _save_phone(conn, tenant_id: str, party_id: str, phone: str) -> str | None:
    """Save one number; the reason it was not saved, or None."""
    async with conn.transaction():
        other = await (await conn.execute(
            "SELECT canonical_name FROM parties WHERE tenant_id = %s AND whatsapp_phone = %s AND id <> %s",
            (tenant_id, phone, party_id))).fetchone()
        if other:
            return f"ye number pehle se {other['canonical_name']} ka hai, save nahi kiya"
        await conn.execute("UPDATE parties SET whatsapp_phone = %s WHERE id = %s AND tenant_id = %s", (phone, party_id, tenant_id))
    return None

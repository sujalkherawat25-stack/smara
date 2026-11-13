"""The owner's own bookkeeping by chat: a sale on credit (udhaar) and money received.

Both are tools the agent calls when the OWNER says so. Nothing is written until the owner taps Approve on the card, and the
card shows exactly what will change. The arithmetic is done here, never by the model:

  record_sale     a new invoice for a customer: amount, credit days, an invoice number (made up if the owner gave none).
  record_payment  money received from a customer. It goes to the oldest open invoice first, or to the one the owner names.

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
    if len(rows) > 1:
        return None, "several customers match: " + "; ".join(r["canonical_name"] for r in rows) + ". Ask the owner which one."
    return (rows[0], None) if rows else (None, None)


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

"""Nightly Tally sync from the owner's PC: validate rows, then update receivables and payables in one transaction."""
from datetime import date, timedelta

from pydantic import BaseModel, Field

from src.ingest.importer import close_invoices, import_rows, open_refs
from src.ingest.sheet_mapper import SheetRow, parse_phone

MAX_ROWS = 5000
MSME_DAYS = {"micro", "small"}


class ReceivableIn(BaseModel):
    party: str = Field(min_length=1, max_length=200)
    phone: str = Field(default="", max_length=25)          # optional: many Tally ledgers have no mobile
    invoice_ref: str = Field(min_length=1, max_length=80)
    amount_rupees: float = Field(gt=0, lt=1e10)
    due_date: date
    gstin: str | None = None


class PayableIn(BaseModel):
    supplier: str = Field(min_length=1, max_length=200)
    bill_ref: str = Field(min_length=1, max_length=80)
    bill_date: date
    amount_rupees: float = Field(gt=0, lt=1e10)
    gstin: str | None = None
    msme_class: str = "unknown"
    agreement_45: bool = False
    paid: bool = False


class SyncBody(BaseModel):
    receivables: list[ReceivableIn] = Field(default_factory=list, max_length=MAX_ROWS)
    payables: list[PayableIn] = Field(default_factory=list, max_length=MAX_ROWS)


def paise(rupees: float) -> int:
    return int(round(rupees * 100))


def deadline(bill_date: date, msme_class: str, agreement_45: bool) -> date | None:
    """43B(h): micro and small suppliers must be paid within 15 days, or 45 with a written agreement."""
    if msme_class not in MSME_DAYS:
        return None
    return bill_date + timedelta(days=45 if agreement_45 else 15)


CLOSE_GUARD_MIN_OPEN = 5      # with fewer open Tally invoices than this, the share rule does not apply
CLOSE_GUARD_SHARE = 0.8       # refuse to close more than this share at once: the export is probably incomplete


async def apply_sync(pool, tenant_id: str, device_id: str | None, body: SyncBody) -> dict:
    """Apply a full Tally snapshot. Open invoices from Tally that the snapshot no longer lists are closed as paid,
    unless the snapshot is empty or would close most of what was open (an incomplete export is not a payment)."""
    rows = [SheetRow(r.party, parse_phone(r.phone), r.invoice_ref, paise(r.amount_rupees), r.due_date, r.gstin)
            for r in body.receivables]
    result = await import_rows(pool, tenant_id, rows, source="TALLY") if rows else {"created": 0, "updated": 0}
    closed, closed_names, skipped = 0, [], None
    if body.receivables:
        keep = {r.invoice_ref for r in body.receivables}
        still_open = await open_refs(pool, tenant_id, "TALLY")
        gone = [o for o in still_open if o["invoice_ref"] not in keep]
        if len(still_open) >= CLOSE_GUARD_MIN_OPEN and len(gone) > CLOSE_GUARD_SHARE * len(still_open):
            skipped = (f"not closed {len(gone)} of {len(still_open)} open invoices: the export would close most of them at once. "
                       "Check the Tally export and sync again.")
        elif gone:
            closed = await close_invoices(pool, gone)
            closed_names = sorted({o["canonical_name"] for o in gone})
    else:
        skipped = "no receivables in this sync, so no invoices were closed"
    async with pool.connection() as conn:
        async with conn.transaction():
            for p in body.payables:
                await conn.execute(
                    """INSERT INTO payables (tenant_id, supplier, supplier_gstin, bill_ref, bill_date, amount_paise,
                                             msme_class, agreement_45, paid_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (tenant_id, bill_ref, supplier) DO UPDATE
                         SET amount_paise = EXCLUDED.amount_paise, msme_class = EXCLUDED.msme_class,
                             agreement_45 = EXCLUDED.agreement_45,
                             paid_at = COALESCE(payables.paid_at, EXCLUDED.paid_at)""",
                    (tenant_id, p.supplier, p.gstin, p.bill_ref, p.bill_date, paise(p.amount_rupees),
                     p.msme_class if p.msme_class in ("micro", "small", "other") else "unknown", p.agreement_45,
                     date.today() if p.paid else None))
            await conn.execute("INSERT INTO tally_syncs (tenant_id, device_id, receivables, payables) VALUES (%s,%s,%s,%s)",
                               (tenant_id, device_id, len(body.receivables), len(body.payables)))
    return {**result, "payables": len(body.payables), "closed": closed, "closed_parties": closed_names[:5],
            "close_skipped": skipped}

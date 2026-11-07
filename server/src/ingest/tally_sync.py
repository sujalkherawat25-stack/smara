"""Nightly Tally sync from the owner's PC: validate rows, then update receivables and payables in one transaction."""
from datetime import date, timedelta

from pydantic import BaseModel, Field

from src.ingest.importer import import_rows
from src.ingest.sheet_mapper import SheetRow

MAX_ROWS = 5000
MSME_DAYS = {"micro", "small"}


class ReceivableIn(BaseModel):
    party: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=10, max_length=15)
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


async def apply_sync(pool, tenant_id: str, device_id: str | None, body: SyncBody) -> dict:
    rows = [SheetRow(r.party, "".join(c for c in r.phone if c.isdigit())[-10:], r.invoice_ref, paise(r.amount_rupees),
                     r.due_date, r.gstin) for r in body.receivables]
    result = await import_rows(pool, tenant_id, rows) if rows else {"created": 0, "updated": 0}
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
    return {**result, "payables": len(body.payables)}

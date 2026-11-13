---
name: gst-purchase-reconciliation
description: Matching the owner's purchase register against the GSTR-2B statement from the GST portal to find missing or mismatched input tax credit, and chasing suppliers. Use when the owner sends a GSTR-2B / purchase register file or asks about ITC, GST mismatches or supplier invoices not showing.
---

# GST purchase reconciliation (purchase register vs GSTR-2B)

## What this is
GSTR-2B is the monthly statement on the GST portal listing the invoices that suppliers have reported, and so the input tax credit (ITC) the owner can claim in that month's GSTR-3B. If a supplier has not filed, or filed wrongly, the owner's ITC is at risk. Reconciling finds those gaps before the GSTR-3B is filed (due on the 20th for most monthly filers, but the owner or CA knows their own date).

## Boundaries (important)
- **You never log in to the GST portal.** Login and OTP are the owner's. The owner (or CA) downloads the GSTR-2B (Excel or JSON) and the purchase register from Tally, and sends the files here. If they ask you to fetch it, explain this and tell them the download steps.
- You prepare a reconciliation; the CA or owner decides what to claim. Do not tell them to claim or reverse ITC; show the evidence and the options.
- Do not message suppliers without the owner's approval (`send_whatsapp_message` asks for it).

## Fast path (use this first)
Call `gst_reconcile(books_file_ref, gstr2b_file_ref)` with the two attached files. One call finds each file's header, matches on GSTIN (or supplier name) plus normalised invoice number, classifies every row, works out the ITC at risk and makes the PDF report. Then:
1. Check `counts_reconcile` is true.
2. `send_file_to_owner` with `report_file_ref`.
3. Tell the owner, in a few lines: invoices in each file, matched, the ITC at risk, and the suppliers to chase (`chase_suppliers`, biggest first). Mention `possible_matches` (a different invoice number, same supplier and tax) as items for a person to confirm, never as matches.
4. Offer one short message per supplier for the owner's approval.
Do not write code for this and do not call `inspect_and_import_file` on a purchase register or a GSTR-2B: they are not receivables. Fall back to the manual procedure below only if `gst_reconcile` returns an error that the files cannot be read.

## Manual procedure (fallback)
1. `read_attachment` / `inspect_and_import_file` to see each file. Use `run_python` with pandas (`mh.read_table` finds the real header row) to load both.
2. Normalise: GSTIN upper-case and trimmed; invoice number upper-case with spaces, slashes, leading zeros and punctuation removed; dates to ISO; amounts to numbers. `check_gstin` can validate a GSTIN's format.
3. Match on **supplier GSTIN + normalised invoice number**. Then compare invoice date and the tax amounts (taxable value, IGST, CGST, SGST).
4. Classify every row:
   - **Matched**: all within Rs 1 rounding.
   - **Amount mismatch**: same invoice, different tax or value. Show both numbers and the difference.
   - **In books, not in 2B**: the supplier has not reported it. This is the ITC at risk.
   - **In 2B, not in books**: possibly not yet entered, a wrong GSTIN, or not the owner's purchase. Do not assume it is eligible.
   - **Possible match**: same GSTIN and amount but an invoice number that differs by a typo. List these separately for a human to confirm; never auto-match them.
5. Print totals for each class and the ITC amount at risk. Check that matched + each unmatched class equals the row counts of the two files. If not, find out why before reporting.
6. Produce the output with `make_document` kind `custom` (blocks: stats, a table per class capped to the most important rows, a note stating the matching rule) and, for the full detail, an Excel via `mh.write_xlsx`. Send both with `send_file_to_owner`.

## Chasing suppliers
For "in books, not in 2B" group by supplier and offer one short message per supplier asking them to file or correct the listed invoices (numbers and dates). Offer it to the owner for approval. Do not threaten; keep it factual.

## Also worth flagging (without giving a verdict)
- ITC is generally allowed only when the invoice appears in GSTR-2B; credit for invoices not paid to the supplier within 180 days of the invoice date has to be reversed unless paid later. Mention the rule and ask the CA to confirm for the specific case.
- Supplier GSTINs that are cancelled or invalid.

## Mistakes to avoid
- Matching on supplier name (names differ; GSTIN does not).
- Reporting totals you did not verify against the file row counts.
- Treating a mismatch as a fraud signal. Most are typos and timing.

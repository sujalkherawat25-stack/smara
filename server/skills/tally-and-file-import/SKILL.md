---
name: tally-and-file-import
description: Getting the owner's receivables and payables into Smara from Tally, Excel, CSV, PDF or photos; fixing files that will not import. Use when the owner sends any data file, asks how to connect Tally, or an import skipped rows.
---

# Getting data in

## Which route
1. **Tally connected (best).** Smara Desktop reads open bills from TallyPrime each night and syncs them. Needs TallyPrime open with its HTTP server enabled (default port 9000) and the PC on. The owner turns it on in the helper; if they ask how, tell them to ask for the "tally-sync" steps from the Smara Desktop settings. Parties need a **mobile number in their Tally ledger** or they are skipped.
2. **A file.** Tally "Bills Receivable" export, Excel (.xlsx/.xls), CSV (comma, semicolon or tab; UTF-8, UTF-16 or Windows encoding all work), or a Tally XML day book via `run_python` (`mh.tally_receivables`).
3. **A photo or PDF.** `read_attachment` gives you the text; a clear bill photo can be added one invoice at a time. Photos are slower and less certain: confirm the party, amount and date with the owner before importing.

## Procedure for a file
1. `inspect_and_import_file(mode="inspect")`. Look at `rows_found`, `sheet_total_rupees`, `skipped` and the `sample`.
2. If rows were skipped, read the reasons:
   - *missing phone*: the file has no mobile numbers. Customers already in Smara are matched by name automatically. For the rest, ask the owner for the numbers (one message listing the names) rather than guessing.
   - *missing due date*: Smara uses bill date + 30 days when there is only a bill date. If neither exists, ask the credit period.
   - *could not find a header row*: the message lists the columns it saw. Tell the owner which columns are needed (customer name, amount pending, and a due date or bill date; phone and invoice number help).
3. **Import in the same turn, without waiting for the owner to agree**: `inspect_and_import_file(mode="import")`. Nothing goes to a customer without the owner's approval, so importing is safe. Ask first only if nothing usable was found, if rows were skipped for a reason only the owner can answer, if the file has credit notes or negative amounts, or if it is a photo (confirm party, amount and date first).
4. Read the result: `totals_match` must be true. If not, say so plainly and investigate before telling the owner it is done.
5. Tell the owner what is now in the ledger: invoices and total, how much is overdue, the three biggest overdue customers, the customers with no WhatsApp number (by name), and what was skipped and why. Then check the total against their own number ("Tally shows 14.2 lakh outstanding; does that match?").

## Traps
- **Totals rows and sub-totals** are skipped, but check the sheet total against the owner's own number ("Tally shows 14.2 lakh outstanding; does that match?").
- **Advance receipts and credit notes** appear as negative or credit amounts. They are not receivables. Mention them; do not import them as bills.
- **Duplicate invoice numbers** across financial years can collide: the importer treats an invoice number as unique per shop. If the owner reuses numbers each year, ask which year the sheet covers.
- **Old files** re-imported later update amounts for unpaid invoices only; paid ones stay paid.
- Files over 100 MB cannot be received over WhatsApp; ask for an export of just the outstanding report.
- Never type a password into a spreadsheet tool or ask for Tally credentials. Smara reads Tally on the owner's own PC.

## After the import
Offer the next step: "Want me to prepare reminders for the 12 customers overdue more than 7 days?" and the owner report (`owner-daily-report`).

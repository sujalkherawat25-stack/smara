---
name: making-documents
description: Creating professional PDF and Word documents for the owner (statements, reminder letters, schedules, reports, custom workings). Use whenever the owner asks for a PDF, a Word file, a statement, a letter or any document to send or print.
---

# Making documents that look professional

## The tool
`make_document(kind, format, ...)` renders a print-quality A4 document (PDF default, or `docx` for a file the owner or CA will edit). It handles Hindi and Gujarati text correctly. Numbers for the built-in kinds come straight from the database, so you do not type them.

| kind | what it makes | needs |
|---|---|---|
| `statement` | A customer's account statement with ageing chart and UPI line | `party_id` |
| `reminder_letter` | A formal payment reminder letter with the overdue invoice table | `party_id`, `body` (the paragraph(s) only; greeting and sign-off are added) |
| `payables_schedule` | Supplier bills with 43B(h) deadlines | nothing |
| `owner_report` | Receivables, ageing, top overdue, collections, payables | `days` (optional) |
| `custom` | Anything else, from blocks you provide | `title`, `blocks` |

After it returns: read `figures`, confirm they match what the owner asked about, then `send_file_to_owner(file_ref)`. Send the file; do not paste its contents. If the owner wants a customer to receive it, tell them where it is and offer to draft the covering WhatsApp message for their approval. Files go to the owner only.

## Custom documents: blocks
`blocks` is a list; each block has a `type`:
- `heading` {text}
- `paragraph` {text}
- `note` {text} (a shaded callout, for assumptions and caveats)
- `stats` {items: [{label, value, tone?: bad|good|warn, hint?}]} up to 4 across; `value` is already-formatted text
- `table` {columns: [{label, kind?: "money", align?}], rows: [[...]], total_row?: [...], caption?}. Give money columns as plain rupee numbers with `kind: "money"`; they are shown as Rs with Indian grouping. Dates as text like "05 Nov 2026".
- `bar_chart` {title, items: [{label, value}], money?: true}
- `keyvalues` {items: [[label, value]]}

Limits: 60 blocks, 500 rows per table, 12 columns. Longer detail belongs in an Excel file.

## Quality rules (these are what separate good from sloppy)
1. **Verify first.** Compute with `run_python`, print the totals, and only then put them in the document. A total row must equal the sum of its rows.
2. **Lead with the answer.** A `stats` block of 2-4 headline numbers at the top, then the detail.
3. **One idea per section**, each with a short heading. No section for the sake of it.
4. **Say what the numbers are based on** in a `note`: the date, the data source, any assumption or estimate. Estimates are labelled as estimates.
5. **Plain language.** Write for the owner or their customer, not for an accountant. Keep customer-facing letters courteous and short.
6. **Names and invoice numbers exactly as in the ledger.** Do not translate or "correct" them.
7. **Language of the reader.** A letter to a Hindi- or Gujarati-speaking customer can be written in that language; the renderer handles the script.
8. **Right file for the request.** PDF means PDF; Word means `docx`; a spreadsheet the owner will sort or edit is an Excel file (`mh.write_xlsx`), not a PDF.
9. After sending, check the page count in the result is sensible (a one-customer statement should be one page; if it is 6, something is wrong).

## If it fails
The result has `error` and sometimes `detail`. A bad `party_id` means look the customer up again. A blocks error names the problem. Fix and retry once; if it still fails, tell the owner plainly rather than sending something else.

---
name: receivables-from-books
description: Working out what customers owe from the owner's exported books (sales invoices, credit notes, receipts and allocations, bank statement) - total receivable, overdue, ageing, over-credit-limit, penal interest, duplicate or bounced receipts. Use whenever the owner sends invoice and receipt files and asks what is outstanding, not for a customer already in the ledger.
---

# Receivables from exported books

Do this in ONE `run_python` script, not many small steps. Files can have thousands of rows: load them with `mh.read_table`, never print them. Print only totals and the few rows you need to check.

## Steps (all inside one script)
1. **Load** invoices, credit notes, receipts, allocations (and the bank statement when it is attached). Print row counts and column names once.
2. **Clean the receipts, only when the owner asks for corrected numbers**:
   - the same bank reference or cheque number twice: keep the earliest, drop the later;
   - a cheque the bank statement shows as returned (`CHQ RTN`, `RETURN`, `BOUNCE` plus the cheque number): treat that receipt as not received.
   Say how many receipts you dropped for each reason.
3. **Balance per invoice** = invoice value - credit notes against that invoice (dated on or before today) - receipts allocated "Agst Ref" to that invoice.
4. **Lump-sum receipts** (types such as "On Account" and "Advance", with no invoice number): attach each to a customer with `mh.match_names(receipt_names, ledger_names)`. Read `review` and the `fuzzy` pairs: a name it could not decide is a question for the owner, not a guess. Then `mh.fifo_apply(invoices, payments)`: oldest invoice first per customer. What is left over comes back as `advances`; report it separately and never net it against another customer.
5. **Only then** total, age, rank or compute interest. Ageing is days past the DUE date, not the invoice date. "Overdue" means today is after the due date.
6. **Check**: sum of open balances + sum of what was paid or credited = sum of invoice values (less advances). If it does not tie, find out why before answering.

```python
import smara_helpers as mh, pandas as pd
inv = mh.read_table(INV); cn = mh.read_table(CN); rv = mh.read_table(RV); al = mh.read_table(AL)
m = mh.match_names(rv[NAME_COL], ledger[LEDGER_COL])          # print m['review'] and m['fuzzy']
# ... build `rows` (party, ref, date, balance) and `pays` (party, amount) ...
res = mh.fifo_apply(rows, pays)       # res['open'], res['advances']
```

## Answering
- Give the figure, then in one line how you got it and what you excluded (for example "2 duplicate receipts and 3 returned cheques removed").
- If two reasonable readings of a rule give different totals (for example whether a lump-sum "advance" is applied to invoices), say which you used and give the other figure in one line. Do not hide the choice.
- The answer is wanted in the owner's format: if they asked for JSON or a table of exact keys, use exactly those keys.

## Mistakes to avoid
- Netting one customer's advance against another customer's invoice.
- Ageing from the invoice date.
- Matching names with `==` only. "Rathod Cash & Carry", "RATHOD CASH AND CARRY" and "Rathod  Cash and Carry" are one customer; "Rathod Traders" and "Rathod Enterprises" are two.
- Printing thousands of rows.

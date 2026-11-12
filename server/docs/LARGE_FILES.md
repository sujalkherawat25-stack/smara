# Large-file test (12 November 2026)

`python -m evals.bigfiles --db <postgres url ending in _eval> --out <dir>` generates owner files with a known truth
(invoices, rupees, customers), parses them, imports them into a throwaway tenant, imports them again, and checks every
number against the truth. No model is called. Cases: CSV, Excel and Tally XML at 1,000 rows; 20,000-row CSV and Excel;
UTF-16 tab-separated CSV with title rows and `1,23,456.00 Dr` amounts; Windows-1252 semicolon CSV with blank lines;
a four-sheet Excel file with text dates; a Tally XML with receipts; and Tally XML near the 20 MB limit.

## First run (before the fixes)

Everything matched to the paisa, and a second import changed nothing, except:

| Problem found | Cause | Fix |
| --- | --- | --- |
| A four-sheet workbook of 30,000 invoices imported 7,500. The other sheets were dropped without a word. | `map_sheet` took the first sheet that had a header row. | Every sheet with a header row is read. The preview lists how many invoices came from each sheet, and a sheet that has data but no receivables columns is named as not read. |
| After a 20,000-row import about 18,000 invoices were due at once, so the owner would be asked about each one. | Overdue rows were all given a first reminder of "now". | Rows already due go out 30 a day, largest amount first (`PACE_PER_DAY`), for at most 30 days (`HORIZON_DAYS`). The owner is told how many days it takes. |
| The first version of that fix spread the 18,000 over 605 days (30 a day), found when the test was rerun on the deployed build. | The spread had no limit. | Past 900 invoices the rest are kept in the ledger with no reminder scheduled, and the owner is told how many. |
| (Found while fixing the first) Two branches that each number invoices from 1001 would overwrite each other once several sheets are read. | The ledger key is the invoice number. | The same invoice listed twice counts once. A different invoice with a reused number is kept with `-2`, `-3` added, and the owner is told to check it. |
| The sheet's own "Grand Total" line was reported as a skipped row. | A total line has a party name and an amount but no due date. | Total lines are ignored. |

## Speed

Parsing is fast (0.3 s for 20,000 CSV rows, 1.8 s for a 30,000-row workbook, 1.6 s and 212 MB peak for a 20.8 MB Tally
file). Importing costs about 4 ms a row in one transaction: 20,000 rows take about 80 s and the 20.8 MB Tally file
(32,913 invoices) about 2 minutes. A typical shop's file is far smaller.

## Size limits and the last run

A 20.8 MB CSV (221,923 invoices, 27,733 customers) imports exactly on a PC: 909 s, 4.1 ms a row, evenly, and again in 646 s.
On the production server the same file had not finished after 30 minutes and held the database at 95% CPU, so it was stopped;
the cause there is not known. Two cheap changes were made on the way and are measured: an index on customer name per shop
(migration 024) and a per-import cache so each customer is looked up once. On a 60,000-row CSV that took the import from
231 s to 127 s and the repeat import from 176 s to 73 s, with every number still exact.

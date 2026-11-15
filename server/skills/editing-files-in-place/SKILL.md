---
name: editing-files-in-place
description: Changing a file the owner already has (Excel workbook, sheet or document): add a column, fix values, add a formula, tidy a sheet. Use whenever the owner sends a file and asks to change, add to, update or fix it, or says "same file but with ...". Keeps every sheet, row, formula and format exactly as it was.
---

# Changing a file the owner sent

The owner's file is theirs. Their sheets, rows, formulas, colours and column widths must come back exactly as they were, with only the asked change added. Rebuilding the file from a dataframe (`pandas.read_excel` then `to_excel` or `mh.write_xlsx`) loses formulas, formatting and rows, and sheets with a title block above the header get read wrongly. Never do that for an edit.

## Steps
1. **Look first.** `run_python` with openpyxl: for each sheet print the name, `max_row`, the header row and the first few rows. Header rows differ between sheets (one may start on row 4). Note which sheets have the column you need; do not assume.
2. **Find the file.** If it is not attached in this message, call `list_my_files`; files sent earlier are kept. Pass its `file_ref` in `file_refs`.
3. **Edit in place.**
   - Adding one column of a formula: `mh.add_column(src, dst, source_header='TotalPremium', new_header='Bonus 40%', formula='={c}{r}*0.4')`. It finds the header per sheet, copies the look, writes live formulas, and reports sheets it skipped.
   - Anything else: `openpyxl.load_workbook(path)` (never `data_only=True`, that replaces formulas with stale numbers), change only the cells asked, save to `./out/` under a new name (`name_with_change.xlsx`). Keep the original file name in the new name.
4. **Compare.** `mh.compare_workbooks(src, dst)` must return `ok: True`. If not, find out why and fix it; do not send. Check that the sheet and row counts equal the original's.
5. **Spot-check the new numbers** yourself (a total computed from the original values) and print them.
6. **Send** with `send_file_to_owner`, and say in plain words: which sheets changed and how many rows got the change, which sheets were left alone and why, and that nothing else was touched (only after step 4 said so).

## If something does not fit
- A sheet has no such column: leave it as it is and say so. Do not invent one.
- Values are text ("1,25,000"): use `mh.parse_amount` inside the formula's check, but keep the original cell as it was.
- The owner says it is wrong: do not defend it. Run `mh.compare_workbooks` against the original and show what differs, then fix it.

---
name: editing-files-in-place
description: Changing a file the owner already has (Excel workbook, sheet or CSV): write a value in a column, fill or clear cells, add a column or formula, or a further change to a file you just sent.
---

# Changing a file the owner sent

1. `open_spreadsheet` the file: exact headers, entry rows, total row, any block below the totals.
2. Decide sheet, column, rows and value from the owner's words. If one of them has two readings that give a different
   file ("Aug collection ko paid kardo": write the word Paid, or fill in the amount due?), `ask_owner` with those options.
3. `edit_spreadsheet`. A follow-up change uses the file_ref of the file you last sent, so the earlier change stays.
4. If the result is not `verified`, send nothing and say why. Otherwise send it and tell the owner the column, the
   number of rows and the side effects it lists (e.g. a Total that now shows #VALUE!), with an offer to fix them.

Only if edit_spreadsheet cannot express the change: run_python with openpyxl (not data_only), then
`mh.compare_workbooks(src, dst, allowed=[letters you changed])` must be ok before sending.

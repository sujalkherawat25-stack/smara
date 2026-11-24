# Document tools

Every Desktop chat and background task has six built-in document tools (MCP server `smara_docs`, `src/smara/native_docs.py`).
They need neither Microsoft Office nor Python on the PC; the libraries ship inside the app.

| Tool | Makes |
|---|---|
| `doc_make_docx` | Word: title, headings, paragraphs, bullets, tables with a total row, pictures, page numbers |
| `doc_make_xlsx` | Excel: header styling, number formats, totals as real formulas, and the computed values returned |
| `doc_make_pptx` | PowerPoint 16:9: cover, bullets, table, picture, speaker notes |
| `doc_make_pdf` | PDF report or letter (same blocks as Word) |
| `doc_make_poster` | One-page poster as PDF plus a PNG for WhatsApp; sizes A4, A3, A5, square, story |
| `doc_read` | Text of a Word, Excel, PowerPoint or PDF file, with formulas and their values, and a layout line for Word |

## Why it is built this way

- **Measured before building.** Asked for a Word report, an Excel sheet and a poster, the agent previously wrote Python scripts
  and needed a toolchain on the PC: 47 to 100 seconds, and two of three jobs did not finish where Python had no pip. With the
  tools: 18 to 33 seconds, two tool steps (make, then read back), no scripts left in the folder.
- **Hindi and other Indian scripts.** A plain PDF library does not shape them (vowel signs land in the wrong place). PDFs and
  posters are therefore printed by the PC's own Edge or Chrome (`docs_html.py`), with the network shut. Without a browser, Latin
  text still works through reportlab and Hindi is refused with a clear message; a Word file always works.
- **Indian numbers.** Formats `inr` (1,84,000) and `rupees` (₹1,84,000), in tables and as Excel number formats.
- **Checked formulas.** Excel totals are written as formulas; the tool evaluates simple ones (SUM, AVERAGE, MIN, MAX, COUNT,
  ROUND, ABS, arithmetic) and returns the numbers, and lists any it could not check, so the agent quotes tool results.

## Safety

Files are written only inside the project folder, never into Smara's state folders, never over an existing file (a taken name gets
" (2)"). `replace: true` overwrites only a file these tools made, and only while it is byte-for-byte what they wrote (a fingerprint
in `.smara/docs-made.json`), building beside it first so a failure keeps the old file. Pictures come only from the project
folder. No network, no macros. Calls are audited with the rest of the agent's work. Text read from files is data, never
instructions.

## Not done yet

Charts in Excel and PowerPoint, editing an existing Word or Excel file in place, mail-merge from a sheet (one letter per row),
and page-image review by the model of the finished file.

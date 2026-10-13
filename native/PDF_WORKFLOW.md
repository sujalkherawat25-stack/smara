# PDF delivery: rendered evidence, not just an existing file

The architecture-report audit found two different failure classes. Earlier
Sarvam turns exhausted output tokens (the v0.1.20 adapter increased the default
allowance). The eventual generator still had a layout bug: `BaseDocTemplate`
kept the cover template on all pages. Dark text on a dark background, dark table
headers and orphan pages survived text-only checks. Headless-browser screenshots
failed under Windows restrictions, and the generator was deleted anyway.

## Maintained workflow

1. Inspect relevant source, current `release/VERSION` and Git revision. Do not
   reuse stale version numbers from chat history or old release documents.
2. Keep the editable generator. Compile before running; write large content in
   bounded sections rather than one oversized tool call. Do not restart source
   discovery after an output limit if that evidence is already saved.
3. For ReportLab, set `autoNextPageTemplate="body"` or explicitly switch templates.
   Set header **Paragraph** styles to white: table `TEXTCOLOR` does not override
   a Paragraph's own style. Keep headings with their content; review pagination.
4. Call `pdf_inspect` for the selected workspace PDF. Follow `next_page` until
   every page of the same content hash has been checked. It is local and
   read-only: no Chrome launch, remote OCR, API key or upload is needed.
5. Use `preview_command_argv` as an explicit native shell action to export PNGs.
   The CLI also exposes `smara pdf FILE --render-dir DIRECTORY --start-page N`
   (four pages per invocation). The frozen Desktop helper supports `--native-pdf`.
   MCP never writes previews outside the shell sandbox.
6. Visually review every rendered page. A text-only chat model cannot consume
   native `view_image` results. A separate `image_analyze` call requires native
   upload approval and may be billed; do not silently switch/upload. If visual
   review cannot be completed, say so rather than claiming a verified design.
7. Fix warnings and re-render changed pages. Preserve the source and deliver the
   PDF, not temporary screenshot scripts or a claim that the file hash proves
   layout quality.

`pdf_inspect` uses bounded PDFium rendering and samples contrast/bounds. Its
warnings are heuristics, not accessibility certification, semantic correctness,
or proof of a perfect layout. Rotated/scanned/image-heavy pages need particular
human attention. It always reports `visual_review_completed=false`.

Goals with declared PDF outputs must have native MCP preflight records covering
all pages of the current file hash without warnings. Older revisions, partial
page ranges and agent assertions do not count. This only satisfies mechanical
preflight; human acceptance remains separate. Ordinary chat is guided by this
workflow, not governed by the goal outcome ledger.

## Regression checks

`tests/test_native_pdf.py` uses real local rendering to test dark-on-dark text,
sparse pages, out-of-bounds text, pagination, changed files, missing renderers,
workspace boundaries, read-only MCP, and explicit PNG export.
`tests/test_native_outcomes.py` tests current-hash and complete-page evidence.
Provider/tool/CLI tests cover discovery and guidance without paid model calls.

`scripts/build_architecture_pdf.py` retains the repaired architecture generator.
It writes a separate reviewed copy under `output/pdf/`, leaving the original
user-supplied report untouched. Its prose is a source description, not a claim
that every capability has passed a live-model or security certification.

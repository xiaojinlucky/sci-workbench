# Layout De-interleaving, Per-Page OCR & Symbol Fidelity Protocol

## 1. Two-Column Vertical Gutter De-interleaving

When `pdftotext -layout` or `pdfplumber(..., layout=True)` extracts two-column scientific PDFs (ACM, IEEE, Nature, Cell, Springer), raw lines interleave left and right columns horizontally on the same text line.

The engine resolves this deterministically per page:
1. Compute the 90th-percentile line width (`p90_width`). Pages narrower than 65 characters stay single-column.
2. Scan candidate gutter positions `col` in `[0.34 * p90_width, 0.66 * p90_width]`:
   - Count lines with non-empty text on both sides of `col` separated by a gap of `>= 3` consecutive spaces straddling `col` (`split_hits`).
   - Penalize lines where `col` cuts through non-whitespace text (`cut_penalties`).
   - Trigger two-column de-interleaving only when `split_hits >= 6`, `split_ratio >= 0.30`, and `score = split_ratio - 0.8 * penalty >= 0.18`.
3. Partition lines into contiguous **full-width bands** (titles, author blocks, wide captions/tables spanning across the gutter) and **two-column bands**.
4. Flush each two-column band strictly as **all left-column lines (top to bottom) -> column break -> all right-column lines (top to bottom)**, tagging each emitted block with `column: "left" | "right" | "full_width"` and `deinterleaved: true`.
5. Repair end-of-line English hyphenation (`word-\ncontinuation` -> `wordcontinuation`) only when the preceding character is alphabetic and the next line starts with a lowercase letter.

## 2. Cross-Page Repeating Header & Footer Stripping

For PDFs with `>= 3` pages, the engine inspects the top 2 and bottom 2 non-empty lines of every page after normalizing digit runs to `<N>`. Any normalized line occurring on `>= max(2, ceil(0.35 * total_pages))` pages—or matching static page-number/DOI/copyright patterns in header/footer zones—is stripped from body text and logged in `stripped_headers_footers[]` with original page and line numbers.

## 3. Strict Per-Page OCR & OSD Rotation Probing

To prevent multi-page batch OCR failures (lost page separators, cross-page text bleed, and corrupted rotated tables):
- Run OCR **only** on pages where a page-aware reader returned empty native text.
- Render one page at a time via `pdftoppm -f P -l P -singlefile -png -r 300`.
- If Tesseract `osd` data is installed, probe orientation with `tesseract <png> stdout --psm 0`:
  - If `Rotate` is `90`, `180`, or `270`, record `rotation_risk: true` and emit `PDF_PAGE_ROTATION_DETECTED`.
  - If `Pillow` is installed, rotate counter-clockwise by `-Rotate` degrees into a temporary deskewed PNG before OCR and mark `auto_deskewed: true`; otherwise keep the raw render and mark `auto_deskewed: false`.
- Keep `native_text` and `ocr_text` strictly separated in `pdf_page_texts[]`. Never silently substitute another language when a requested language is missing.

## 4. Long-PDF Coverage & Continued-Table Rules

- **Coverage Audit**: Independently record `pages_seen`, `native_text_pages`, `ocr_text_pages`, `first_page_covered`, `mid_page_covered`, `last_page_covered`, and `tail_10pct_coverage_ratio`. A smooth summary of page 1 never proves tail-page coverage.
- **Continued Tables**: Link table candidates across consecutive pages when either an explicit marker (`Table N (continued)` / `Cont.` / `续表`) appears or column headers match identically on page `P` and `P+1`.

## 5. Symbol Fidelity & Garbled-Font Audit

- Preserve bullet (`•`, U+2022) and middle dot (`·`, U+00B7) distinctly without character folding.
- Preserve Greek letters, mathematical operators (`± ≤ ≥ × ÷ ≈ ≠`), and superscript/subscript characters verbatim.
- Count `U+FFFD` replacement characters and `(cid:N)` artifacts; any positive count triggers `PDF_GARBLED_FONT_DETECTED` so affected pages are routed for visual/OCR inspection.

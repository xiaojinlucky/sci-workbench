# Semantic Cross-Reference, Layout, and PDF Visual Inspection Rules

## 1. Two-Layer Cross-Reference Verification

A LaTeX document can compile with exit code 0 and zero undefined references while still pointing to the **wrong** section, figure, or table. Guard against this on two distinct levels:

1. **Deterministic Author-Declared Targets (`reference_expectations.json`)**:
   - When the task or author explicitly declares the intended target for a specific `\ref`/`\eqref`/`\autoref`/`\cref` on a given `source` file and `line`, serialize that contract to `/app/work/reference_expectations.json` and pass `--reference-expectations`.
   - Any mismatch raises hard gap `TEX_REFERENCE_TARGET_MISMATCH` even when a valid PDF was produced.
   - Never guess or infer unstated targets into `reference_expectations.json`.
2. **Automated Static Prefix & Self-Reference Audit (`bib_and_ref_audit`)**:
   - **Duplicate `\label` (`TEX_DUPLICATE_LABEL`)**: Two definitions of the same label silently break numbering; always disambiguate.
   - **Prose vs Prefix Mismatch (`TEX_REF_TYPE_MISMATCH`)**: Writing `Section~\ref{fig:workflow}` or `Table~\ref{sec:method}` is a deterministic mismatch between prose noun and label namespace.
   - **Self-Section Context Warning (`semantic_ref_warnings`)**: Inside `\section{Results}\label{sec:results}`, writing `"the method steps in Section~\ref{sec:results}"` triggers an explicit review warning without silently rewriting author intent.

## 2. Two-Column Layout, Widow/Orphan & Overfull Clipping Audit

For conference/journal two-column classes (`acmart`, `IEEEtran`, `sigconf`, `twocolumn`):

- **Widows & Orphans**:
  - Set `\widowpenalty=10000` and `\clubpenalty=10000` in the preamble (when not prohibited by the publisher class) to discourage single stranded lines at column/page boundaries.
  - Inspect `pdf_inspection.widow_orphan_warnings` for orphan headings stranded at page bottom or trailing final pages with `<= 3` lines.
- **Overfull `\hbox` and Table/Figure Clipping**:
  - Any `Overfull \hbox >= 6.0pt` indicates visible margin bleed or column collision in two-column layouts.
  - **Figures**: Constrain width explicitly (`\includegraphics[width=\linewidth]{...}` in single column or `0.95\columnwidth` in two-column; use `figure*` only for true full-width panels).
  - **Tables**: Prefer `booktabs` + `tabularx` (`p{...}` or `X` columns) and concise column headers instead of blind `\scalebox` shrinking that makes text illegible.
  - **Equations**: Break long equations across lines with `amsmath` `align` or `multline` environments instead of overflowing the column margin.

## 3. Real PDF Inspection Checklist

Before marking a manuscript `ok`:
1. Confirm `pdf_generated: true` and valid `%PDF-` header.
2. Confirm `pdf_inspection.pages >= 1` and `unresolved_markers_in_pdf == []` (no rendered `??` or `[?]`).
3. Confirm `non_embedded_fonts == []` (when `pdffonts` is available).
4. Inspect rendered `previews/page-*.png` to verify no clipped tables/figures, no missing glyphs, and clean column/page breaks.
5. Keep `/input` untouched byte-for-byte; every edit lives in `/app/output/editable/` with a unified diff recorded in `change_log`.

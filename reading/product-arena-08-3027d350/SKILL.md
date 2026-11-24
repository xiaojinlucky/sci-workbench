---
name: scientific-doc-pdf-parser
description: Parse scientific PDFs and text documents into grounded reading-order blocks, multi-level section trees, tables (with continued-table linking), numbered formulas, captions, and symbol-fidelity audits. Enforces gutter-based two-column de-interleaving, cross-page header/footer stripping, strict per-page native-vs-Tesseract OCR separation with OSD rotation probing, long-PDF tail-page coverage verification, and dual Harbor + topic-markdown deliverables.
---

# Scientific Document & PDF Parsing (`product-arena-08-3027d350`)

## 1. Purpose, Scope & Core Guarantees

This skill implements the Harbor and `llm_judge_topic_markdown` dual contract for scientific document parsing (`阅读01｜文献文档解析`) under strict offline Linux constraints (`64` requests, `900s`, zero network, zero `fitz`/`pymupdf`/`cairosvg`/`win32com`).

It targets the four official evaluation pillars directly through deterministic offline scripts rather than prose-only prompts:

1. **Extract Completely (提得全)**: Separates native PDF text from per-page scanned OCR text, strips cross-page repeating headers and footers into a traceable log (`stripped_headers_footers`), preserves figure/table captions, footnotes, and references, and runs an explicit long-PDF coverage audit (`first_page_covered`, `mid_page_covered`, `last_page_covered`, `tail_10pct_coverage_ratio`) to eliminate silent tail-page truncation.
2. **Order Accurately (排得对)**: Applies vertical whitespace-gutter projection (`deinterleave_page_columns`) to layout-preserved PDF text (`pdftotext -layout` / `pdfplumber`), separating full-width elements (title, abstract, spanning tables) from two-column body zones and reordering columns strictly left-to-right, top-to-bottom while repairing line-wrap hyphens safely.
3. **Structure Cleanly (拆得清)**: Builds a multi-level `section_hierarchy`, extracts CSV/Markdown/layout-aligned PDF tables, links consecutive continued tables (`continued_table_candidates`), extracts LaTeX and numbered equations, and audits fragile scientific symbols (`•` U+2022 vs. `·` U+00B7, Greek letters, math operators, and `U+FFFD` / `(cid:N)` garbled fonts).
4. **Ground Verifiably (核得回)**: Every emitted block carries a 1-based original PDF `page`, `column` role, page-local `line_span`, and `extraction_method` (`native` or `tesseract`). Unreadable, encrypted, rotated, or empty pages are logged explicitly in `gaps` and never fabricated.

## 2. Environment & Hard Boundaries

- Locate scripts relative to this `SKILL.md` directory (typically `/skills/weekly-skill/scripts/`).
- **Allowed PDF/OCR stack (already installed only; never `pip install`)**: `pdfplumber`, Poppler (`pdftotext -layout`, `pdfinfo`, `pdftoppm`), `pypdf`, `tesseract` (`--psm 0` OSD + `--psm 3` text), and optional `Pillow` for deskewing rotated scans.
- **Forbidden libraries**: Never import or invoke `fitz`, `pymupdf`, `cairosvg`, or Windows COM modules.
- **Strict Per-Page OCR Isolation**:
  - Never batch-merge multiple pages into one OCR call (prevents dropped page separators).
  - Never overwrite or mix `ocr_text` into `native_text`; keep both fields separate in `document_parsing_bundle.json`.
  - Never infer page numbers or request OCR when no page-aware PDF reader is available (`page_selection_status: page_metadata_unavailable`).
  - Default OCR language is `eng`; pass `--ocr-langs eng+chi_sim` only after confirming `chi_sim` appears in `preflight_probe` / `tesseract --list-langs`.
- **No Fabricated Native Traces**: `trace/trace.jsonl` is owned by the evaluation platform. List it under `platform_managed_artifacts` in `result.json`; never synthesize fake traces.
- **No `.tsv` Submission Assets**: Keep all skill resources UTF-8 text (JSON/MD/PY/CSV), `<= 50` files, `<= 500 KB` per file, `<= 550 KiB` total.

## 3. Execution Workflow (Deterministic 5-Step Pipeline)

### Step 1: Preflight Capability Probe & Early Result Initialization

Probe actual `/input` files, installed PDF/OCR tools, available Tesseract language packs, and initialize budget tracking and early `result.json` state so timeouts never leave empty deliverables:

```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B scripts/budget_guard.py --init --state /app/work/.budget_state.json
python3 -B scripts/result_guard.py --init --path /app/output/result.json --case-id "<CASE_ID>"
python3 -B scripts/block_indexer.py --scan /input --out /app/work/blocks.json
```

Read `/input/case.json` and `/input/task.md` first. Inspect `/app/work/env.json` to confirm whether `pdftotext`, `pdfinfo`, `pdftoppm`, `tesseract`, and required languages (`eng`, `chi_sim`, `osd`) exist before choosing `--ocr-langs`.

### Step 2: Run Document Parsing Engine (Layout + OCR + Tables + Formulas + Symbol Audit)

```bash
python3 -B scripts/doc_parser_engine.py \
  --input /input \
  --output /app/output \
  --work /app/work \
  --case-id "<CASE_ID>" \
  --ocr-langs eng
```

Verify the engine outputs all dual-contract artifacts under `/app/output/`:
- `result.json` (Harbor contract + observed metrics, provenance, layout/coverage/symbol audits, gaps)
- `document_parsing_bundle.json` (full unclipped blocks, per-page native & OCR text, stripped headers/footers)
- `parsed_document.md` (auditable Markdown report with per-block `[block_id | p.X | column | Lstart-Lend | method]` anchors)
- `arm_manifest.json`, `characterization.json`, `src/reproduce.py`, `execution/run.log`, `execution/results/summary.json`, `README.md` (topic-markdown contract)

### Step 3: Post-Parse Quality Inspection & Targeted Refinement (Agent Step)

Inspect `/app/output/parsed_document.md` and `/app/output/document_parsing_bundle.json` against the case task requirements in `/input/task.md` using `references/document_parsing_qa_checklist.md`:

1. **Coverage Gate**: Check `page_coverage_audit.per_pdf` — confirm `first_page_covered`, `mid_page_covered`, `last_page_covered`, and `tail_10pct_coverage_ratio`. If any page is listed in `empty_or_unreadable_pages`, inspect its `ocr_status` (`tools_unavailable`, `language_unavailable`, `render_failed`, `no_text_detected`) and report the exact gap rather than guessing content.
2. **Column Order Verification**: For pages with `detected_columns == 2`, inspect the left/right transition in `parsed_document.md` to confirm sentences flow cleanly without column interleaving. If a complex 3-column or boxed sidebar page shows residual cross-column fragments, flag that page explicitly in `gaps` and verify against the raw page text in `pdf_page_texts[]`.
3. **Table & Continued-Table Verification**: Inspect `tables[]` and `continued_table_candidates[]`. Verify header-to-column alignment, multi-level headers, units, and continued-table continuity across page boundaries. Preserve empty cells as empty; never backfill missing values from adjacent rows.
4. **Formula & Symbol Fidelity Check**: Inspect `symbol_fidelity_audit`. Confirm `•` (U+2022) and `·` (U+00B7) were not collapsed into one character, Greek letters and subscripts/superscripts survived intact, and `garbled_font_detected` is `false`. If `U+FFFD` or `(cid:N)` tokens appear, render only those affected pages via `pdftoppm` and cross-check via OCR or visual inspection.
5. **Rotated Scan Check**: If `PDF_PAGE_ROTATION_DETECTED` appears in `gaps`, check whether `auto_deskewed` succeeded (requires Pillow); if `false`, explicitly advise manual page review before citing numbers from that page.
6. **Structural 10-Element Review**: Confirm whether title, authors/affiliations, abstract, section hierarchy, captions, tables, formulas, footnotes, references, and stripped headers/footers are accurately separated.

### Step 4: Deterministic Reproducibility Verification

Run the emitted `src/reproduce.py` inside `/app/output` to confirm `execution/results/summary.json` stays 100% consistent with `result.json` and `document_parsing_bundle.json`:

```bash
python3 -B /app/output/src/reproduce.py --case-id "<CASE_ID>" --output-dir /app/output
```

### Step 5: Path Closure, Grounding Gate & Final Audit

```bash
python3 -B scripts/path_closure_check.py --root /app/output --out-json /app/work/path_closure.json --strict
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output \
  --require result.json \
  --require parsed_document.md \
  --require document_parsing_bundle.json \
  --require arm_manifest.json \
  --require characterization.json \
  --require src/reproduce.py \
  --require execution/run.log \
  --require execution/results/summary.json
```

`final_audit.py` treats absent `trace/trace.jsonl` as a non-blocking warning because `result.json` declares it under `platform_managed_artifacts`; any missing locally owned artifact fails the audit.

## 4. Exception & Degradation Decision Matrix

| Failure Condition | Engine Behavior | Required `gaps` Code | Status |
| --- | --- | --- | --- |
| Encrypted / password-protected PDF | Stop extraction; preserve SHA-256 and `pdfinfo` page count if readable | `PDF_ENCRYPTED_UNREADABLE` | `partial_with_gaps` |
| No page-aware PDF reader installed | Emit `page_selection_status: page_metadata_unavailable`; request `[]` OCR pages | `PDF_PAGE_TEXT_EXTRACTION_UNAVAILABLE` | `partial_with_gaps` |
| Image-only page, OCR tools missing | Keep native pages intact; emit no fabricated blocks for empty page | `PDF_PAGE_OCR_TOOLS_UNAVAILABLE` | `partial_with_gaps` |
| Requested OCR language missing | Report `available_languages` vs `missing_languages`; do not silently substitute | `PDF_PAGE_OCR_LANGUAGE_UNAVAILABLE` | `partial_with_gaps` |
| OCR succeeds on scanned page | Store in `ocr_text` separately; tag blocks `extraction_method: "tesseract"` | `PDF_OCR_TEXT_UNVERIFIED` | `partial_with_gaps` |
| OCR returns empty string | Never label page as blank; keep original page unknown | `PDF_PAGE_OCR_NO_TEXT_DETECTED` | `partial_with_gaps` |
| Rotated scan detected by OSD | Log rotation angle; deskew via Pillow if present, else mark unverified | `PDF_PAGE_ROTATION_DETECTED` | `partial_with_gaps` |
| `U+FFFD` or `(cid:N)` font errors | Count affected tokens; isolate page for targeted visual/OCR check | `PDF_GARBLED_FONT_DETECTED` | `partial_with_gaps` |

## 5. References & Minimal Examples

- `references/output_contract_spec.md` — Dual Harbor + `llm_judge_topic_markdown` artifact schema and status rules.
- `references/layout_ocr_and_symbol_protocol.md` — Gutter de-interleaving, header/footer stripping, per-page OCR/OSD, continued-table linking, and symbol fidelity specification.
- `references/document_parsing_qa_checklist.md` — 10-element paper anatomy and pre-delivery QA checklist.
- `examples/minimal_example.md` — Synthetic minimal invocation and annotated output schema example.

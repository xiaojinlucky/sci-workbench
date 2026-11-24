# Scientific Document & PDF Parsing Skill (`product-arena-08-3027d350`)

Offline, zero-third-party-dependency Python 3.12 skill for structured scientific PDF and text document parsing under the Harbor + `llm_judge_topic_markdown` dual contract.

## Key Capabilities

- **Two-Column Gutter De-interleaving**: Automatically detects two-column layouts on `pdftotext -layout` / `pdfplumber` pages via vertical whitespace projection, keeps full-width titles/abstracts/tables intact, reorders body text strictly left-to-right column-by-column, and safely repairs end-of-line English hyphenation.
- **Cross-Page Header/Footer Stripping**: Detects repeating running headers, footers, page numbers, and DOI lines across pages and moves them into a traceable `stripped_headers_footers` log.
- **Per-Page Native vs. OCR Separation + OSD Rotation Probing**: Invokes `pdftoppm` and `tesseract` strictly one empty-text page at a time (never batch-merges pages), checks page rotation via `--psm 0` when `osd` is installed, keeps `native_text` and `ocr_text` strictly separated, and reports exact tool/language/rotation/empty-page gaps.
- **Long-PDF Coverage & Symbol Fidelity Audits**: Explicitly tracks `first_page_covered`, `mid_page_covered`, `last_page_covered`, and `tail_10pct_coverage_ratio`; audits `•` vs `·`, Greek letters, math operators, and `U+FFFD` / `(cid:N)` garbled-font tokens.
- **Multi-Level Structure Extraction**: Builds `section_hierarchy`, separates figure/table captions and footnotes, extracts CSV/Markdown/PDF-layout tables with consecutive-page continued-table linking, and captures numbered/LaTeX equations.

## Quick Start

```bash
python scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python scripts/doc_parser_engine.py --input /input --output /app/output --work /app/work --case-id CASE_01 --ocr-langs eng
python /app/output/src/reproduce.py --case-id CASE_01 --output-dir /app/output
python scripts/path_closure_check.py --root /app/output --out-json /app/work/path_closure.json --strict
python scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python scripts/final_audit.py --dir /app/output --require result.json --require parsed_document.md --require document_parsing_bundle.json --require arm_manifest.json
```

## Requirements & Boundaries

- Runtime: Python 3.12+ standard library only.
- Optional system tools (used automatically when preinstalled; never installed at runtime): Poppler (`pdftotext`, `pdfinfo`, `pdftoppm`), Tesseract OCR (`tesseract`, `eng`, `osd`), `pdfplumber`, `pypdf`, `Pillow`.
- Prohibited libraries: Zero dependency on `fitz`, `pymupdf`, `cairosvg`, or `pywin32`.
- See `SKILL.md`, `references/`, and `examples/minimal_example.md` for complete contracts and synthetic usage examples.

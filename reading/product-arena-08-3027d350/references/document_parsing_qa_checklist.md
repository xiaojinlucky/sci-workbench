# Document Parsing QA & 10-Element Completeness Checklist

Run this checklist after `doc_parser_engine.py` completes and before finalizing `/app/output/result.json`.

## A. Four Core Quality Gates

1. **Coverage Gate (No Silent Truncation)**
   - [ ] `page_coverage_audit.total_pdf_pages_seen` matches `pdfinfo` / actual input PDF page count.
   - [ ] `first_page_covered`, `mid_page_covered`, and `last_page_covered` are verified; any missing page appears in `empty_or_unreadable_pages` and `gaps[]`.
   - [ ] `page_separator_integrity` is `"per_page_verified"` (zero cross-page merged OCR calls).

2. **Reading-Order Gate (No Column Interleaving)**
   - [ ] Two-column pages report `deinterleaved: true` and clean `left -> right` block progression in `parsed_document.md`.
   - [ ] Repeating headers, footers, page numbers, and running journal titles are removed from paragraph prose and logged in `stripped_headers_footers`.
   - [ ] Sentence joins at line wraps do not fuse unrelated columns or swallow captions.

3. **Structure & Symbol Gate**
   - [ ] `section_hierarchy` preserves multi-level numbering (`1`, `1.1`, `2.3.1`) and standard academic sections (Abstract, Introduction, Methods, Results, Discussion, References).
   - [ ] Tables preserve original headers, row counts, and empty cells; consecutive-page continuations are listed in `continued_table_candidates`.
   - [ ] Formulas retain equation numbers and original mathematical symbols; `symbol_fidelity_audit.garbled_font_detected` is checked and any `U+FFFD` / `(cid:N)` page is flagged.
   - [ ] `•` (U+2022) and `·` (U+00B7) remain unmerged.

4. **Grounding & Contract Gate**
   - [ ] Every block in `reading_order_blocks` carries `block_id`, `source_file`, `page` (1-based for PDFs), `column`, `line_span`, and `extraction_method`.
   - [ ] All dual-contract artifacts exist (`result.json`, `parsed_document.md`, `document_parsing_bundle.json`, `arm_manifest.json`, `characterization.json`, `src/reproduce.py`, `execution/run.log`, `execution/results/summary.json`, `README.md`).
   - [ ] `trace/trace.jsonl` is declared under `platform_managed_artifacts` and NOT fabricated.
   - [ ] `src/reproduce.py`, `path_closure_check.py --strict`, `gap_gate.py`, and `final_audit.py` all pass.

## B. Scientific Paper 10-Element Anatomy Verification

When the input is a research article, verify structural separation across:
1. Title & front-matter metadata (`block_type: heading/paragraph`, p.1)
2. Authors, affiliations & correspondence footnote (`footnotes[]`)
3. Abstract (`structural_completeness.has_abstract`)
4. Introduction / research gap framing (`section_hierarchy`)
5. Methods / materials / experimental models (`section_hierarchy`)
6. Results & quantitative findings (`reading_order_blocks`)
7. Tables & continued tables (`tables[]`, `continued_table_candidates[]`)
8. Figures & table captions (`captions[]`)
9. Numbered / display formulas (`formulas[]`)
10. Discussion, limitations, data/code availability & References (`has_references_section`)

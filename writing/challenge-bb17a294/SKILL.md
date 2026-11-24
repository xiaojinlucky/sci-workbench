---
name: bilingual-academic-translation
description: Scholarly EN-ZH translation and bilingual parallel writing workflow with native PDF/DOCX/MD/TXT auto-extraction from /input. Trigger when translating or aligning scientific manuscripts, patents, abstracts, tables, or references between English and Chinese while auto-extracting source blocks, glossary drafts, and reference ledgers, enforcing exact magnitude/unit/order parity (with attested clause reordering), cell-level Markdown table alignment, live citation and bilingual bibliography 1:1 integrity, sentence-level negation scope and hedge conservation, anti-translationese prose, and a complete 5-field Harbor result.json.
---

# Bilingual Academic Translation and Parallel Writing

Translate or align scholarly manuscripts between English and Chinese with zero drift across numbers, magnitude units, SI units, Markdown tables, live citations, bilingual reference entries, equations, sentence-level negation polarity, modal hedges, and locked terminology—starting directly from raw `/input/` PDF, DOCX, or text/Markdown files without manual preprocessing.

## Scope and Hard Invariants

1. **Native `/input/` extraction & automated term/reference bootstrapping**: Never read raw `.pdf` or `.docx` files as plain UTF-8 bytes and never hand-author `glossary.json` or `reference_map.json` from scratch. Run `bilingual_alignment_guard.py --extract-terms-and-refs --from-input /input --work-dir /app/work` (backed by zero-dependency `scripts/doc_extract.py`: `pdftotext` preserving physical empty pages -> `pypdf` -> `pdfplumber` -> pure-stdlib `zlib` PDF stream fallback; pure-stdlib OOXML `.docx` parser preserving headings, Markdown tables, `footnoteReference` -> `[^id]`, and detecting OOXML `w:instrText` / `w:fldChar` Zotero/CSL/Mendeley/EndNote/Citavi/Word REF live citation fields; safe multi-encoding text reader) to persist `/app/work/source_extracted.txt`, `/app/work/source_blocks.json`, `/app/work/glossary_draft.json`, and `/app/work/reference_map_draft.json` containing the immutable `source_citation_ledger`. Whenever DOCX live citation fields are converted to Markdown plain text, explicitly report `citation_fields_preserved: false` and never claim live citation links are preserved.
2. **Zero numeric, range, magnitude, unit, table-cell, or silent-order drift (`Decimal`-exact)**: Preserve every numerical value, precision, percentage, statistical test (`p < 0.001`, `95% CI [1.12, 1.85]`, `n = 128`), scientific notation (`1.5e-4` ↔ `1.5×10^-4`), explicit numeric range (`40-60`, `12.5–18.0%`, `10~20 mg/L`, `-0.2-28.4`), and SI unit (`μg/mL`, `mA/cm²`, `mol/L`, `rpm`) via exact `decimal.Decimal` comparison. Convert EN↔ZH magnitudes accurately (`1.5万` = `15,000`, `2.3亿` = `230 million`, `1.2千` = `1,200`) without rounding. Audit Markdown tables cell-by-cell (respecting escaped `\|` inside cells). Preserve numeric order per paragraph; when natural EN↔ZH syntax requires reordering clauses, attest it explicitly with `reordered_clause_note` (never silently swap group/condition numbers).
3. **Immutable `source_citation_ledger`, live citation, heading hierarchy 1:1, display, and equation integrity**: Lock every source reference into an immutable `source_citation_ledger` (`cite_key`, original raw text, `source_doi`, `source_page`/`source_line`), keeping `translated_title` strictly decoupled as a display-only field. Any target entry absent from source (`unverified_external_additions`) or any unauthorized completion of a missing/truncated source DOI (`unverified_doi_completions`) is strictly forbidden from entering `verified_dois`/`verified_citations` and forces `passed=false` (non-zero exit). Cross-entry translated title swaps across Chinese/source references are deterministically blocked (`reference_title_swap`). Preserve 1:1 section heading hierarchy (`H1/H2/H3` count, depth, and structural information — never silently merge, drop, or simplify headings; any mismatch triggers `HEADING_HIERARCHY_DRIFT`). Preserve every in-text citation key, display identifier (`Fig. 1` ↔ `图 1`, `Table 2` ↔ `表 2`, `Eq. (3)` ↔ `式 (3)`), and LaTeX math block (`$...$`, `$$...$$`).
4. **Sentence-level negation scope and modal-hedge conservation**: Preserve negation polarity at both paragraph and matched-sentence level (`not`/`never`/`without` ↔ `未`/`不`/`并非`/`无`) so mixed-polarity paragraphs cannot hide local polarity flips. Preserve epistemic limiters (`may`/`suggests`/`associated with`/`only` ↔ `可能`/`提示`/`相关`/`仅`) and block correlational-to-causal overclaim escalation (`proves`/`causes`/`证明了`/`直接导致`).
5. **Editable sentence-and-paragraph alignment + complete 5-field contract**: Export `/app/output/bilingual_aligned.md` with sentence-level Markdown comparison tables (`S1..Sn` + page/line source anchors + verification badges), explicitly report any excess `UNAUDITED` paragraphs instead of silently truncating, bound style warnings for long manuscripts (`--max-style-warnings`), and seal `/app/output/result.json` with `case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`, honest `status`, `sources`, `uncertainty`/`gaps`, and `artifacts`.

## Reference Pointers (Load on Demand)

- Magnitude conversion rules, statistical/unit normalization, `reordered_clause_note` syntax, negation/hedge calibration, and bilingual reference mapping: read `references/bilingual-fidelity-rules.md`.
- Academic Chinese & English anti-translationese and natural scholarly prose rules: read `references/academic-chinese-style.md`.
- Exact 5-field `result.json` schema and pre-seal QA checklist: read `references/output-contract-and-qa.md`.

## Execution Workflow

### Step 1: Probe Environment, Auto-Extract `/input` Source, and Bootstrap Glossary & References
Run M1-M4 probes and invoke the automated `/input` extractor so PDF/DOCX/MD/TXT inputs are normalized with page/line anchors before translation:
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>" --task-id "bilingual-academic-translation"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
python3 /skills/weekly-skill/scripts/bilingual_alignment_guard.py \
  --extract-terms-and-refs \
  --from-input /input \
  --work-dir /app/work \
  --out-json /app/work/extraction_summary.json
```
**Completion criterion**: `/app/work/source_extracted.txt`, `/app/work/source_blocks.json`, `/app/work/glossary_draft.json`, `/app/work/reference_map_draft.json`, and skeleton `/app/output/result.json` exist; actual source format, page count, and `case_id` are verified from `/input/case.json` and `/input/task.md`.

### Step 2: Review and Lock Glossary and Reference Mapping Ledger
1. Read `references/bilingual-fidelity-rules.md` and `references/academic-chinese-style.md`.
2. Inspect `/app/work/glossary_draft.json` (auto-populated with abbreviation definitions, recurring EN/ZH domain terms, and frequency counts). Fill canonical `target` translations and `forbidden_variants` for domain terms and save to `/app/work/glossary.json`.
3. Inspect `/app/work/reference_map_draft.json` whenever references require cross-script alignment. Confirm `target_author` and `target_title` entries, preserving `UNVERIFIED_EXTERNAL_REF_METADATA` for Chinese references lacking official English metadata/DOI, and save to `/app/work/reference_map.json`.

**Completion criterion**: `/app/work/glossary.json` locks recurring domain terms; `/app/work/reference_map.json` is confirmed whenever cross-script reference conversion applies.

### Step 3: Execute Positive Bilingual Academic Translation & Editable Alignment
1. Translate `/app/work/source_extracted.txt` block-by-block (following `P1..Pn` in `/app/work/source_blocks.json`) into `/app/output/translated.md`, maintaining exact block/paragraph count, 1:1 section heading hierarchy (`H1/H2/H3` depth and complete heading semantics without merging or dropping headings), sentence coverage, Markdown table row/column parity, captions, footnotes, equations, and source-grounded reference list (never add external references or silently complete truncated DOIs).
2. Apply explicit **EN→ZH scholarly restructuring rules** (never word-for-word calques):
   - **Unpack stacked modifiers**: Break long English relative/participial clauses into chronological or cause-effect Chinese sub-clauses with explicit subjects rather than stacking 3+ `的` modifiers.
   - **Surface implicit logic**: Make implicit transitions (`While`, `Whereas`, `Given`, `Despite`) explicit (`尽管…但…`, `相比之下`, `鉴于`) without escalating correlation into causation.
   - **Replace passive/nominalized translationese with direct domain verbs**: Convert `performed an evaluation of` / `was observed to exhibit a significant reduction` into `评估了` / `显著下降`; eliminate `扮演……角色`, `以一种……方式`, `值得注意的是`, `不是……而是……`, and excessive `——`.
   - **Anchor technical terms & symbols**: On first mention, anchor discipline terms with canonical English/abbreviations (`骨形态发生蛋白 6（BMP6）`), keeping gene symbols (`TP53`, `ANXA2`), chemical formulas, and software names untranslated.
3. Apply explicit **ZH→EN scholarly register rules**:
   - **Convert parataxis to hypotaxis**: Restructure Chinese topic-comment comma chains into clear English main clauses plus subordinate condition/scope clauses.
   - **Enforce IMRaD tense & modality discipline**: Past tense for specific Methods/Results, present tense for established mechanisms and `Fig.`/`Table` pointers; preserve exact epistemic strength (`associated with`, `suggests`, `may reflect` — never `proves`/`causes`).
4. **Tables, captions, and inline math discipline**:
   - Preserve exact Markdown table row/column structure, numeric precision, SD/95% CI ranges, and SI units in every cell.
   - Keep `Fig. X` ↔ `图 X`, `Table X` ↔ `表 X`, `Eq. (X)` ↔ `式 (X)` consistent; keep `$...$` / `$$...$$` LaTeX math verbatim.
5. Whenever natural EN↔ZH syntax legitimately changes numeric order within a paragraph while keeping every group/condition value bound to its entity, record a `reordered_clause_note` in `/app/work/glossary.json` or `<!-- reordered_clause_note: ... -->`.

**Completion criterion**: `/app/output/translated.md` covers 100% of extracted blocks, sentences, tables, captions, equations, and references in idiomatic scholarly prose with zero semantic or numeric drift.

### Step 4: Run Deterministic Bilingual Alignment, Table, Citation, and Style Guard
Run `bilingual_alignment_guard.py` directly against the original `/input/<SOURCE_FILE>` (with `--source-format auto`) or `/app/work/source_extracted.txt`:
```bash
python3 /skills/weekly-skill/scripts/bilingual_alignment_guard.py \
  --source /input/<SOURCE_FILE> \
  --source-format auto \
  --extracted-source-out /app/work/source_extracted.txt \
  --target /app/output/translated.md \
  --glossary /app/work/glossary.json \
  --case-id "<CASE_ID>" \
  --out-json /app/output/translation_audit.json \
  --out-aligned /app/output/bilingual_aligned.md \
  --out-contract-patch /app/work/contract_patch.json
```
Add `--references /app/work/reference_map.json` whenever bilingual reference metadata applies. Inspect `blocking_issues`, `unaudited_paragraphs`, `unverified_external_additions`, `unverified_doi_completions`, `heading_hierarchy_check`, and `style_warning_summary`; resolve all blocking codes (`empty_source_extraction`, `HEADING_HIERARCHY_DRIFT`, `paragraph_count_mismatch`, `table_alignment_drift`, `numeric_or_unit_drift`, `numeric_order_swap_unattested`, `pvalue_drift`, `citation_drift`, `unverified_external_addition`, `unverified_doi_completion`, `bilingual_citation_script_mismatch`, `reference_doi_or_year_swap`, `reference_title_swap`, `display_numbering_drift`, `equation_drift`, `negation_polarity_shift`, `negation_scope_shift`, `causal_overclaim_escalation`, `translation_truncation`, `glossary_conflict`) and re-run until `passed` is `true` (exit code `0`).

**Completion criterion**: `bilingual_alignment_guard.py` exits `0` with `"passed": true`, zero `blocking_issues`, and fresh `/app/output/translation_audit.json`, sentence-level `/app/output/bilingual_aligned.md`, and `/app/work/contract_patch.json`.

### Step 5: Seal 5-Field `result.json` and Run Grounding + Final Audits
1. Read `references/output-contract-and-qa.md`. Merge `/app/work/contract_patch.json` with `deliverable`, `sources`, `uncertainty`, and `artifacts` into `/app/output/result.json` via `result_guard.py --finalize`.
2. Run M5 grounding verification and M6 final audit:
```bash
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --patch-json '<PATCH_JSON>' --finalize
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py --dir /app/output \
  --require translated.md \
  --require bilingual_aligned.md \
  --require translation_audit.json
```
**Completion criterion**: `result_guard.py --validate`, `gap_gate.py`, and `final_audit.py` all exit `0`, and `/app/output/result.json` contains all 5 `output_contract` fields with honest status.

## Exception and Edge-Case Handling

- **Scanned or image-only PDFs**: `doc_extract.py` detects empty text layers and raises `empty_source_extraction` with a clear warning instead of fabricating content; log `SCANNED_PDF_NO_TEXT_LAYER` in `gaps` and set `status` to `"partial_with_gaps"` if OCR is unavailable.
- **PDF/DOCX paragraph boundary divergence**: Never silently drop trailing paragraphs; inspect `unaudited_paragraphs` in `translation_audit.json` to reconcile paragraph merges/splits, or use `--para-mismatch-policy ratio` when minor PDF line-wrap splits keep alignment ratio within `[0.90, 1.10]` while auditing excess blocks explicitly.
- **Legitimate EN↔ZH clause reordering**: Verify every condition/group keeps its exact value, then add an explicit `reordered_clause_note` so the guard records `"verified_reordered"`.
- **Ambiguous or missing English metadata for Chinese references**: Preserve the original Chinese reference record alongside transliterated metadata and log `UNVERIFIED_EXTERNAL_REF_METADATA` in `gaps` rather than guessing an official English title or DOI.
- **Conflicting terms or encoding anomalies**: Multi-encoding detection (`utf-8-sig`/`utf-8`/`gb18030`/`latin-1`) prevents decode crashes; document chosen canonical terms in `glossary.json` and genuine source contradictions in `uncertainty`/`gaps`.

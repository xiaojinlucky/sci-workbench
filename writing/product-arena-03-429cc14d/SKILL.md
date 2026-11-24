---
name: references-and-citation-management
description: >-
  Use when inserting, renumbering, deduplicating, formatting, migrating, or auditing
  academic citations and bibliographies across Markdown, LaTeX, BibTeX (.bib), RIS (.ris),
  CSL-JSON, and OOXML Word (.docx) manuscripts. Inspects Word/Google Docs Zotero live
  citation fields vs flattened plain text, synchronizes sequential numeric and author-year
  citations (including figure/table captions), merges preprint/formal and bilingual duplicates
  while protecting homonymous authors, formats GB/T 7714-2015 and APA 7th offline, and
  strictly separates metadata existence from claim support. Do NOT fabricate missing DOIs or
  claim an uninspected paper supports a sentence.
---

# References & Citation Management Skill

Target task: `academic-citation-management` (References & Citation Management). Deliver synchronized, editable manuscripts and bibliographies with full source traceability, OOXML live-field integrity verification, explainable deduplication, and two-layer verification.

## Trigger Boundaries

- **Trigger when**: the user needs citation placement/anchoring, sequential or author-year renumbering after edits, multi-database bibliography deduplication (`.bib`/`.ris`/CSL-JSON), format conversion (GB/T 7714-2015 or APA 7th), Word↔Google Docs Zotero live-field auditing, or citation-to-claim verification.
- **Hard Red Lines**:
  - Never invent missing DOIs, page ranges, volumes, or publication years; annotate `[MISSING_FIELDS: ...]`.
  - Never treat visible `[1]` text in `.docx` as proof of intact Zotero/EndNote/Citavi live fields.
  - Never equate "record exists in bibliography" with "source text supports this sentence's claim".

## Core Workflow Capabilities

1. **Sentence-Level Citation Placement (`挂得对`)**
   - Anchor citations immediately after the specific empirical claim or clause requiring attribution, never dumped indiscriminately at paragraph ends or adjacent captions.
   - Support multi-source single-claim (`[1-3]`) and multi-claim single-source attribution without losing source legends.
2. **OOXML Live-Field vs. Flattened Plain-Text Audit (Word ↔ Google Docs Pain Point)**
   - Inspect `word/document.xml` directly via `scripts/citation_lifecycle_manager.py` (powered by `writing_io_and_invariants.inspect_docx_ooxml`) to distinguish real field instructions (`ADDIN ZOTERO_ITEM`, `CSL_CITATION`, `EN.CITE`, `CITAVI`, `MENDELEY`) from flattened plain text.
   - Report explicit `live_field_status`: `intact` | `flattened_plain_text` | `mixed` | `no_citations_detected`.
   - When flattened/mixed is detected, attach the official Zotero cross-processor transfer & recovery protocol (`references/citation_standards_and_migration.md`).
3. **Bidirectional Synchronization & Renumbering (`编得顺`)**
   - Synchronize in-text citations (`[1]`, `[1-3, 5]`, `(Author et al., 2023)`, `\cite{}`), figure/table captions, and end-of-manuscript reference lists in exact first-appearance order.
   - Detect duplicate reference numbers, dangling in-text citations, orphan uncited bibliography items, non-sequential ordering, and **multi-marker → single-source collapse** (e.g., 20 citation markers backed by only 1 structured source object).
4. **Multi-Format Parsing & Explainable Deduplication (`收得净`)**
   - Parse `.bib`, `.ris`, and CSL-JSON with zero external dependencies.
   - Deduplicate via explicit, auditable criteria: normalized DOI match, normalized title + author overlap, **preprint vs. peer-reviewed formal publication** (preferring formal publication while preserving preprint DOI), and bilingual/alternative titles.
   - **Homonymous-Author Protection**: never merge two records solely by shared surname and year when titles/DOIs differ; explicitly log `protected_homonymous_pairs`.
5. **Dual-Standard Offline Formatting & Two-Layer Verification (`对得上`)**
   - Generate publication-ready **GB/T 7714-2015** (`[J]`/`[C]`/`[M]`/`[D]`/`[EB/OL]`, CJK vs. English author/et al. punctuation) and **APA 7th** lists plus `cleaned_references.bib`.
   - Emit two strictly independent fields per citation:
     - `metadata_existence_status`: `verified_in_library` | `missing_from_library` | `incomplete_metadata` | `retracted_or_eoc`
     - `claim_support_status`: `supported_by_supplied_excerpt` | `partially_supported` | `unverified_fulltext_not_supplied` | `mismatch_or_unsupported`

## Runtime Execution Workflow (Offline Linux Harbor)

All scripts run on Python 3.12+ stdlib with zero third-party packages.

### Step 1: Preflight, Budget, Result Init & Input Scan
```bash
python3 -B /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B /skills/weekly-skill/scripts/budget_guard.py --state /app/work/budget.json --init
python3 -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "academic-citation-management"
python3 -B /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/blocks.json
```

### Step 2: Execute Citation Lifecycle Audit, Deduplication, Renumbering & Formatting
```bash
python3 -B /skills/weekly-skill/scripts/citation_lifecycle_manager.py \
  --manuscript /input/<manuscript_or_docx> \
  --bib /input/<refs_1.bib> \
  --bib /input/<refs_2.ris> \
  --excerpts-dir /input/<optional_excerpts_dir> \
  --source-mapping /input/<optional_source_mapping.json> \
  --out-dir /app/output \
  --case-id "<CASE_ID>"
```
- Review `/app/output/citation_audit_report.md` and `/app/output/citation_audit.json`.
- If the task asks to produce an edited `.docx` or revised Markdown manuscript with inserted/reordered citations, update the draft and re-run `citation_lifecycle_manager.py --strict` until closure passes (or remaining genuine source gaps are explicitly documented in `gaps` and `change_log`).

### Step 3: Final Grounding & Package Audit
```bash
python3 -B /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json \
  --require citation_audit_report.md --require citation_audit.json --require cleaned_references.bib
```

## Exception & Boundary Handling

- **Offline DOI/API absence**: never hallucinate CrossRef/PubMed verification; mark `claim_support_status: unverified_fulltext_not_supplied` when source full text/abstract is absent.
- **Retracted or Expression of Concern items**: flag `metadata_existence_status: retracted_or_eoc` and block use as unflagged primary factual evidence.
- **Corrupt `.docx` or malformed bibliography entries**: report exact parse error honestly and continue auditing intact inputs.

## Output Contract

See `references/output_contract_spec.md`. Required deliverables under `/app/output/`:
1. `citation_audit_report.md` and `citation_audit.json`
2. `cleaned_references.bib`, `formatted_references_gbt7714.md`, and `formatted_references_apa7.md`
3. `renumbered_manuscript.md` (when manuscript input is provided)
4. `result.json` (preserving `case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`, plus Harbor audit fields)

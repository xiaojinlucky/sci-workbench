---
name: cross-lingual-lit-retriever
description: Execute auditable bidirectional cross-language scientific literature retrieval across multilingual fulltexts, publisher translations, abstract-only mirrors, and exact duplicates using controlled terminology expansion, diacritic folding, acronym and gene symbol locking, false-friend back-translation checks, and strict canonical work deduplication without overwriting original titles or merging homonyms with distinct DOIs. Use for multilingual literature search and cross-lingual evidence alignment.
---

# Cross-Lingual Scientific Literature Search (`challenge-802a7115`)

## 1. Mission, Scope & Epistemic Boundaries

Retrieve, align, deduplicate, and verify multilingual scientific literature over finite local corpora (bilingual journal HTML/JSON/PDF such as SciELO, multilingual RIS/BibTeX/CSV exports, and explicit bidirectional known-item research questions in `/input/case.json` and `/input/task.md`).

### Core Guarantees
1. **Genuine Non-English Recall Without Machine-Translation Drift**: Expand queries through controlled, domain-anchored multilingual glossaries and diacritic folding rather than naive full-sentence machine translation.
2. **Exact Per-View Attribution**: Preserve `work_id`, `record_id`, `source_file`, `doi`, `scielo_pid`, `version`, `body_language`, and canonical `view_type` for every retrieved document.
3. **Strict Work-Level Count Conservation**: Consolidate `original_fulltext`, `publisher_full_translation`, `english_abstract_only`, and `exact_duplicate` views under a single canonical `work_id` so translated or duplicated views never inflate unique study counts.
4. **Immutable Original Bibliographic Fields**: Keep `original_title` and `raw_title` untouched byte-for-byte; store multilingual titles in `titles_by_language` and `translated_title` separately; enforce a hard barrier against merging homonyms carrying distinct normalized DOIs.

### Non-Negotiable Out-of-Scope Boundaries
- Never claim live remote database coverage, unseen languages, or additional independent studies beyond the supplied finite corpus snapshot.
- Never treat an `english_abstract_only` or `metadata_multilingual_record` view as sufficient proof of full-text methods, statistical models, or sample subgroups when full body text is absent.
- Never claim external commercial translation API execution in an offline sandbox; document deterministic glossary/cognate expansion and residual manual back-translation checks transparently.
- Never fabricate DOIs, titles, quotes, or language tags from filenames alone—always inspect the actual file contents.

## 2. Methodological Backbone (Broad-Then-Screen, Three Perspectives & W1-W8)

This Skill integrates the shared evidence-anchored protocol (`references/search_methodology_protocol.md`) with cross-lingual retrieval safeguards:

- **Broad-Then-Screen (Screening, Not Cherry-Picking)**:
  - Phase A (Recall Expansion): Expand each research question across verified disciplinary equivalents in every observed corpus language (`en`, `pt`, `zh`, `es`, `de`, `fr`) plus case-provided glossaries so regional/non-English primary studies are not silently missed.
  - Phase B (Transparent Screening): Apply deterministic word-boundary acronym locks, false-friend back-translation checks, and 4-class view consolidation while preserving complete audit logs of every excluded duplicate and separated homonym.
- **Three-Perspective Cross-Verification**:
  1. *Method / Originator Perspective*: Prioritize the primary-language `original_fulltext` to anchor original terminology, definitions, and numerical outcomes before relying on translated editions.
  2. *Practitioner / Domain-User Perspective*: Account for regional spelling, diacritic omission (e.g., `intoxicações` vs `intoxicacoes`, `pressões respiratórias` vs `pressoes respiratorias`), and zero-translation scientific identifiers (`TP53`, `BCL2`, `HBB`, `CO2`, `PImax`, `PEmax`).
  3. *Independent Evaluator Perspective*: Audit every hit for false-friend cognates (e.g., Portuguese `data` = calendar date, `atual` = current, `resumo` = abstract), short-acronym collisions (`SC`, `PD`, `MI`, `OR`), and verbatim sentence grounding (`verified_in_source: true`).
- **Front-End Clarity vs Back-End Auditability (W3 & W5)**:
  - Present domain-clear findings, canonical work inventories, and verbatim multilingual quotes in `/app/output/cross_lingual_report.md`.
  - Keep deterministic SHA-256 title hashes, glossary expansion mappings, and hash-chained logs in `/app/output/cross_lingual_alignment.json` and `/app/work/audit_log.json`.
- **Epistemic Five-State Discipline (W8)**:
  - Classify claim-to-evidence support strictly as `SUPPORTED`, `PARTIAL`, `REFUTED`, `OUTDATED`, or `UNKNOWN` (`scripts/evidence_state_guard.py`), never silently upgrading uncertain cross-lingual matches.

## 3. Five-Step Execution Workflow

Locate all scripts relative to this `SKILL.md` directory (`scripts/`). Perform working inspection under `/app/work` and emit final deliverables under `/app/output`.

### Step 1: Preflight, Input Inventory & Language Boundary Lock
Run environment probing, budget initialization, and structural indexing before interpreting any record:
```bash
python scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python scripts/budget_guard.py --state /app/work/.budget_state.json --init
python scripts/block_indexer.py --scan /input --out /app/work/blocks.json
```
Read `/input/case.json` and `/input/task.md` directly and lock four boundaries:
1. **Format Inventory**: Enumerate actual files (SciELO ArticleMeta JSON, JATS/SciELO HTML, PDF, RIS, BibTeX, CSV, Markdown) and note any `pdf` with `text_layer_status != "full_text"`.
2. **Observed Languages**: Record the exact set of languages actually represented in the corpus (`observed_languages`); mark any unrepresented language as out-of-snapshot.
3. **Question Directionality**: Identify each query's `source_language` and target corpus languages (`en -> pt`, `pt -> en`, `zh -> en/pt`, etc.).
4. **Glossary Seed**: Check whether `/input/case.json` supplies a domain `glossary` dictionary to merge with the built-in controlled glossary.

### Step 2: Controlled Multilingual Terminology Expansion & Back-Translation Check
Execute the pure-stdlib cross-lingual matcher:
```bash
python scripts/cross_lingual_matcher.py --input /input --output /app/output --work /app/work
```
Verify the following five terminology invariants in `/app/output/cross_lingual_alignment.json`:
1. **Controlled Concept Alignment**: Every matched concept lists `canonical_concept`, `domain`, `triggered_by`, `aligned_by_language`, and `back_translation_verified`.
2. **Explicit Per-Language Query Expressions**: Each query emits `target_language_queries` keyed by observed language (e.g., `en`, `pt`, `zh`, `es`) so an independent reviewer can re-run or audit every search term without opaque sentence translation.
3. **Diacritic Folding Without Source Mutation**: NFKD combining-mark stripping matches unaccented queries against accented Portuguese/Spanish/French/German full texts (`necrose avascular`, `intoxicações exógenas`, `pressões respiratórias máximas`) while extracted quotes retain exact UTF-8 source characters.
4. **Zero-Translation Lock**: Gene symbols, protein symbols, chemical formulas, and clinical indices (`TP53`, `BCL2`, `HBB`, `BMP6`, `CO2`, `PImax`, `PEmax`) are never translated and match strictly on token boundaries.
5. **Short-Acronym & False-Friend Protection**: Short uppercase acronyms (`<= 4` chars: `SC`, `PD`, `MI`, `OR`, `SCD`, `AVN`, `CI`, `HR`) match only on strict case-aware word boundaries—blocking substring false hits (`OR` inside `for`, `SC` inside `school`, `MI` inside `micro`)—and automatically emit `false_friend_or_acronym_risk` entries listing competing senses.

### Step 3: Canonical 4-Class View Consolidation (`3 works / 6 views` Boundary)
Audit `canonical_works` in `/app/output/cross_lingual_alignment.json` to confirm exact view taxonomy and work-level count conservation:

| View Class | Selection Criterion | Increments `unique_work_count`? | Eligible for Full-Text Verification? |
| --- | --- | --- | --- |
| `original_fulltext` | Primary-language full body (`>= 300` chars) matching declared/inferred `original_language` | Yes (1 per `work_id`) | Yes |
| `publisher_full_translation` | Full-text translation of the same canonical DOI/PID in another language | No | Yes (with translation attribution) |
| `english_abstract_only` | English abstract mirror without full body text (`abstract_only_non_english` for other langs) | No | No (triggers `ABSTRACT_ONLY_LANGUAGE_VIEWS`) |
| `exact_duplicate` | Same language and identical normalized content digest within the same canonical work | No | No (logged in `exact_duplicates_flagged`) |
| `metadata_multilingual_record` | Bibliographic metadata sidecar (e.g., SciELO ArticleMeta JSON) without full body text | No | No (enriches `titles_by_language` & `original_language`) |

Identity binding follows strict non-corrupting precedence:
1. **Normalized DOI** (`https://doi.org/` stripped, lowercased, trailing punctuation stripped).
2. **SciELO PID** (`S\d{4}-\d{4}\d{13}`) when DOI is absent on one view, strictly subject to the distinct-DOI barrier.
3. **Explicit `work_id` / `canonical_work_id`** when non-empty DOIs do not conflict.
4. **Exact normalized content digest** within the same language when persistent identifiers are absent.

### Step 4: Anti-Corruption Checks — Title Non-Overwrite, Homonym Isolation & False-Friend Review
Perform three mandatory verification passes on `/app/output/cross_lingual_alignment.json`:
1. **Original-Title Immutability (`title_overwrite_audit`)**:
   - Confirm `overwritten_count == 0` and `audit_passed == true`.
   - Confirm native titles remain intact in `raw_title` and `original_title`, while bilingual/translated titles reside cleanly in `titles_by_language` and `translated_title`.
   - Never allow an English publisher translation title to overwrite a Portuguese/Chinese/Spanish primary title.
2. **Homonym Distinct-DOI Barrier (`homonym_distinct_doi_separated`)**:
   - Confirm that records sharing identical or near-identical titles (`title_jaccard >= 0.85`) but carrying **distinct normalized DOIs** remain in separate `work_id` buckets and are explicitly logged with `reason: "distinct_doi_blocks_homonym_merge"`.
3. **Contextual Disambiguation of Flagged Risks**:
   - Inspect every entry in `false_friend_or_acronym_risk` (short acronyms, false friends like PT `data` [date] or `atual` [current], and romanized pinyin/proper-noun tokens).
   - Read the corresponding `evidence_anchors[].quote` to verify the match reflects the intended scientific concept rather than a competing sense; downgrade or mark `PARTIAL` / `UNKNOWN` when context cannot disambiguate.

### Step 5: Sentence-Level Evidence Alignment, Grounding Verification & Final Audit
Inspect `/app/output/cross_lingual_report.md` and `/app/output/result.json` to confirm:
- Every matched work carries `work_id`, `doi` / `scielo_pid`, `original_title`, `languages_present`, `matched_terms`, `matched_views[]`, `excluded_duplicate_views[]`, and verbatim `evidence_anchors[]` (`quote`, `body_language`, `view_type`, `source_file`, `verified_in_source`).
- Every query separates `matched_unique_work_count`, `matched_non_duplicate_view_count`, and `excluded_duplicate_views_count`.
- Every numeric metric in `result.json["metrics"]` has a matching entry in `metric_provenance`.

Execute the mandatory grounding and deliverable gates:
```bash
python scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python scripts/final_audit.py --dir /app/output --result-file result.json --require cross_lingual_alignment.json --require cross_lingual_report.md
```
Do not finalize if `final_audit.py` reports any blocking failure, ungrounded DOI, or missing metric provenance.

## 4. Pre-Mortem Risk Matrix & Exception Handling

| Priority | Failure Mode | Early Observable Signal | Preventive Guardrail | Fallback / Honest Degradation |
| --- | --- | --- | --- | --- |
| P0 | Translated title overwrites original native title | `raw_title != original_title` or missing PT/ZH title | Immutable `raw_title` + `titles_by_language` + SHA-256 audit | Re-read raw source file; fail audit if `overwritten_count > 0` |
| P0 | Homonymous papers with distinct DOIs merged | Two distinct DOIs inside one `work_id` | Hard distinct-DOI merge barrier before PID/title matching | Keep separate `work_id`s and log in `homonym_distinct_doi_separated` |
| P0 | Translations or exact duplicates inflate study count | `unique_work_count` equals view count despite shared DOIs | DOI/PID canonical grouping + 4-class view taxonomy | Exclude `exact_duplicate` from hit totals; report views separately |
| P1 | Short acronym (`OR`/`SC`/`PD`/`MI`) causes substring false positives | Hits inside `for`, `school`, `micro`, `update` | Strict case-aware `\b` boundary matching | Emit `false_friend_or_acronym_risk` and require quote check |
| P1 | False-friend cognate or pinyin drift corrupts relevance | PT `data` (date) matched as English `data` | Built-in false-friend & pinyin drift registry | Flag risk in output; downgrade unverified matches to `PARTIAL`/`UNKNOWN` |
| P1 | Abstract-only view overstated as full-text proof | Full-text claim cited from `english_abstract_only` | Automatic `ABSTRACT_ONLY_LANGUAGE_VIEWS` gap logging | Restrict conclusion to abstract scope; note missing native full text |
| P2 | Empty/scanned PDF or unseen language requested | `text_layer_status != "full_text"` or 0 hits in target lang | `corpus_loader.py` text-layer probe + `observed_languages` | Report honest `UNKNOWN` / `partial` status without fabricating records |

## 5. Self-Check Checklist Before Finishing

Before ending a run, verify every item below against `/app/output/result.json` and `/app/output/cross_lingual_alignment.json`:
- [ ] `case_id` matches `/input/case.json` verbatim and `status` is `"ok"` (or honest `"partial"` with documented gaps).
- [ ] `title_overwrite_audit.overwritten_count == 0` and `title_overwrite_audit.audit_passed == true`.
- [ ] No `canonical_works[]` entry mixes two distinct normalized DOIs.
- [ ] Every `exact_duplicate` view is excluded from `matched_unique_work_count` and listed under `exact_duplicates_flagged`.
- [ ] Every query hit includes at least one verbatim sentence quote with `verified_in_source == true`.
- [ ] Abstract-only views, short acronyms, false friends, and finite-snapshot scope limits are explicitly documented in `gaps` and `cross_lingual_report.md`.
- [ ] `gap_gate.py` returns 0 ungrounded identifiers and `final_audit.py` passes with 0 blocking failures.

## 6. Script CLI Reference & Deterministic Invariants

Each bundled script under `scripts/` is pure Python 3.10+ stdlib and requires zero network calls:

| Script | Purpose | Required Flags / Default Behavior |
| --- | --- | --- |
| `scripts/preflight_probe.py` | Environment & corpus probe | `--input /input --output /app/output --out /app/work/env.json` |
| `scripts/budget_guard.py` | Phase & budget ledger | `--state /app/work/.budget_state.json --init` / `--tick <phase>` |
| `scripts/block_indexer.py` | Section/paragraph block index | `--scan /input --out /app/work/blocks.json` |
| `scripts/cross_lingual_matcher.py` | End-to-end multilingual matcher | `--input /input --output /app/output --work /app/work` |
| `scripts/gap_gate.py` | DOI/arXiv & metric grounding | `--verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json` |
| `scripts/final_audit.py` | Deliverable completeness gate | `--dir /app/output --result-file result.json --require cross_lingual_alignment.json --require cross_lingual_report.md` |

### Worked Execution Verification Pattern (Bilingual Journal Corpus)
When `/input` contains bilingual journal articles (e.g., SciELO ArticleMeta JSON + `_en.html` + `_pt.html` + optional PDF/abstract mirrors):
1. `corpus_loader.py` extracts `titles_by_language`, `abstracts_by_language`, `body_language`, `doi`, and SciELO PID without overwriting any source title.
2. `cross_lingual_matcher.py` binds all language views sharing the same normalized DOI or SciELO PID under one `work_id`, labels `original_fulltext` vs `publisher_full_translation` vs `english_abstract_only` vs `exact_duplicate`, and blocks merging any homonym carrying a distinct DOI.
3. For a Portuguese query lacking diacritics (e.g., `pressao respiratoria maxima em criancas`) or a Chinese query (e.g., `外源性中毒`), controlled glossary expansion generates explicit per-language queries (`target_language_queries`), recalls both native and translated views, deduplicates to unique works, and quotes verified verbatim sentences from the source HTML/JSON/PDF.
4. For queries containing gene symbols (`BMP6`, `KLOTHO`, `TP53`) or short acronyms (`SC`, `PD`, `MI`, `OR`), zero-translation and strict word-boundary rules prevent substring contamination while surfacing explicit disambiguation alerts.

### Stopping Discipline & Budget Control
- Execute the deterministic pipeline once per input corpus, inspect flagged risks (`false_friend_or_acronym_risk`, `homonym_distinct_doi_separated`, `ABSTRACT_ONLY_LANGUAGE_VIEWS`), and enrich findings/gaps only from verified source passages.
- Stop iterating once every query has verified sentence anchors, all four canonical view classes are accurately reconciled, and `gap_gate.py` + `final_audit.py` exit with code `0`.

### Required JSON Key Verification Table
Verify these mandatory keys exist and carry valid values before final submission:

| Artifact | Required Top-Level Keys | Hard Assertion |
| --- | --- | --- |
| `result.json` | `case_id`, `status`, `canonical_works`, `exact_duplicates_flagged`, `title_overwrite_audit`, `homonym_distinct_doi_separated`, `cross_lingual_query_results`, `metrics`, `metric_provenance`, `findings`, `gaps`, `artifacts` | `status == "ok"` (or honest `"partial"`), all metrics finite & grounded |
| `cross_lingual_alignment.json` | `case_id`, `scope_note`, `observed_languages`, `summary`, `title_overwrite_audit`, `homonym_distinct_doi_separated`, `canonical_works`, `exact_duplicates_flagged`, `cross_lingual_query_results` | `title_overwrite_audit.overwritten_count == 0` |
| `cross_lingual_report.md` | Corpus summary, canonical work table, homonym audit, per-query bidirectional evidence anchors, honest boundaries | Non-empty UTF-8 Markdown, zero placeholder tokens |
| `/app/work/audit_log.json` | `chain_integrity`, `entries[]` with `previous_entry_hash` | Valid SHA-256 chain via `verify_audit_chain` |

## 7. Deliverables, References & Minimal Example

- **Final Outputs (`/app/output/`)**:
  - `result.json` — Harbor contract deliverable with `case_id`, `status`, `canonical_works`, `exact_duplicates_flagged`, `title_overwrite_audit`, `homonym_distinct_doi_separated`, `cross_lingual_query_results`, `metrics`, `metric_provenance`, `findings`, `gaps`, `artifacts`.
  - `cross_lingual_alignment.json` — Full structured alignment, glossary trace, and verified sentence anchors.
  - `cross_lingual_report.md` — Human-readable bilingual/multilingual report ready for reviewer inspection.
- **Audit Trail (`/app/work/`)**: `env.json`, `.budget_state.json`, `blocks.json`, `gaps.json`, `audit_log.json` (SHA-256 hash-chained).
- **Supporting Protocols (`references/`)**:
  - `references/cross_lingual_terminology_protocol.md` — Diacritic folding, zero-translation symbols, false-friend table, 4-class view rules.
  - `references/output_contract_spec.md` — Required fields and quantitative invariants.
  - `references/search_methodology_protocol.md` — Shared evidence-anchored search invariants.
- **Minimal Synthetic Example (`examples/`)**:
  - `examples/sample_input_case.json` and `examples/sample_output_schema_example.json` illustrate the input/output interface only and are explicitly marked synthetic.

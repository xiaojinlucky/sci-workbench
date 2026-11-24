---
name: academic-lit-search-engine
description: Evidence-bound academic literature retrieval over finite scholarly snapshots with element-card decomposition, two-round controlled acronym/co-occurrence expansion, zero-hit postings diagnosis, three-perspective annotation, 4-tier transparent ranking, source-error/pagination/empty tri-triage, and auditable provenance. Use for academic literature search tasks.
---

# Academic Literature Search (`challenge-a2909a72`)

## 1. Mission & Non-Negotiable Boundaries
This Skill guides an offline research Agent (DeepSeek Harness) through a reproducible, evidence-bound academic literature search over organizer-authorized snapshots (`/input/case.json`, `task.md`, bibliographic API dumps, and any local full-text documents).

**Hard Boundaries (Never Violate):**
1. **Finite Snapshot Only**: Operate strictly on files inside `/input`. Never claim live remote database recall, global literature completeness, or post-snapshot latest-publication novelty.
2. **No Fabricated Records or Identifiers**: Every DOI, arXiv ID, PMID, author, year, venue, and quote in `/app/output/result.json` must trace to an exact file and JSON pointer in `/input`.
3. **Screening, Not Cherry-Picking**: Never drop counter-examples, negative findings, or borderline records silently. Preserve full PRISMA count conservation (`raw_input == canonical_unique + duplicates_merged_away`) and explicit reason codes for every tier and exclusion.
4. **Never Treat Errors as Zero Hits**: Malformed/truncated JSON files and HTTP/cursor error payloads (e.g., PyAlex #103 invalid cursor URL on final page) are **failures**, never valid empty results. Only cleanly parsed empty arrays (`[]`) qualify as `valid_empty_sources`.
5. **Hard Anti-False-Merge Guard**: Two records sharing an identical or near-identical title that carry **distinct formal (non-preprint) DOIs** must NEVER be silently merged; route them to `ambiguous_candidates` for human arbitration.
6. **Pure Offline Python 3.12 Stdlib**: Zero network access and zero prohibited binary/GUI dependencies.

---

## 2. Quickstart Execution Protocol (DeepSeek Harness)
Locate all scripts relative to this `SKILL.md` directory (`scripts/`). Execute the end-to-end deterministic pipeline first, then inspect and verify its structured outputs before finalizing your narrative response.

### Step 1 — Preflight, Budget Init & Core Pipeline
```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 -B scripts/academic_search_pipeline.py --input /input --output /app/output --work /app/work
```
This single command executes:
- Structure-agnostic intake across Crossref (`work` / `work-list`), OpenAlex (`results` + `abstract_inverted_index`), EuropePMC (`resultList`), PubMed JSON/XML, arXiv Atom XML, BibTeX/RIS/NBIB, CSV, and local documents.
- Source-health tri-triage (`source_load_errors` vs `pagination_issues` vs `valid_empty_sources`).
- Research element-card decomposition and dependency-ordered sub-questions (`SQ0`..`SQ6`).
- Two-round controlled retrieval (`Round 1 core` -> `term_gap_diagnosis` -> `Round 2 expansion` with per-sub-question marginal gain).
- Traceable deduplication (`search_core.dedup`) with `source_count_conserved`, `ambiguous_candidates`, `version_pairs`, and ASReview #328 `rollback_log`.
- Three-perspective tagging, 4-tier transparent ranking (`core / method-only / background / excluded`), deterministic tie-breaking, and Leave-One-Term-Out (LOO) top-k rank stability.
- Atomic emission of `/app/output/result.json`, `results_ranked.csv`, `evidence_chains.json`, `query_log.json`, `report.md`, `audit_log.json`, and `run_manifest.json`.

### Step 2 — Independent Grounding & Deliverable Audit (M5 + M6)
```bash
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json \
  --require results_ranked.csv --require evidence_chains.json \
  --require query_log.json --require report.md
```
Both commands must exit `0`. If `gap_gate.py` reports any `UNGROUNDED_IDENTIFIER` or `MISSING_METRIC_PROVENANCE`, inspect `/app/work/gaps.json` and correct the offending field before exiting.

---

## 3. Step-by-Step Methodological Protocol

### Phase A — Research Element Card & "Screening, Not Cherry-Picking" Launch
Before running or interpreting queries, decompose the user's research question into two explicit lenses (see `query_log.json:element_card`):
1. **Classic Problem Variant Framing**: Identify the core scientific problem archetype (e.g., catalyst durability under harsh operating conditions, biomarker specificity, mechanism comparison) rather than blindly concatenating raw question words.
2. **7-Slot Element Card** (keep unspecified slots explicitly as `UNKNOWN`; never hallucinate constraints):
   - `research_object` (material, organism, model, molecule, or target system)
   - `mechanism_or_outcome` (degradation pathway, efficacy, selectivity, failure mode)
   - `method_or_intervention` (experimental, computational, or analytical technique)
   - `comparator` (explicit baseline or competing alternative, e.g., Ir vs Ru oxide)
   - `population_or_context` (operating conditions such as acidic PEM electrolysis, cohort)
   - `time_scope` (explicit year bounds derived from question or snapshot reference year)
   - `evidence_requirement` (empirical device test, DFT, systematic review, benchmark)
3. **Dependency-Ordered Sub-Questions**:
   - `SQ0` (if known DOI/arXiv/PMID/author is given): anchor resolution prerequisite.
   - `SQ1`: core object + scope prerequisite.
   - `SQ2..SQ5`: parallel facet probes (mechanism/outcome, method origin, comparator, operating conditions).
   - `SQ6`: evidence-type / evaluation verification after `SQ1`.

### Phase B — Two-Round Controlled Query Planning & Acronym/Co-occurrence Expansion
- **Round 1 (Unexpanded Core Recall)**: Score all deduplicated canonical records using strictly unexpanded stemmed core terms (`LocalSearcher(items, abbrevs=[])`) via field-weighted BM25 (`title=2.4`, `abstract=2.2`, `keywords+mesh+venue=1.8`). Never copy stopwords (`what`, `or`, `and`, `since`) into query terms.
- **Term Zero-Hit / Postings Diagnosis (`term_gap_diagnosis`)**: Between Round 1 and Round 2, inspect postings for every core and sub-question term across title, abstract, keywords/venue, linked full text, and corpus-mined abbreviation variants. Classify every term into one of four mutually exclusive diagnostic states:
  1. `metadata_present` — direct postings exist in bibliographic metadata.
  2. `fulltext_only_metadata_blindspot` — zero hits in title/abstract/keywords, but verified hits in local extracted full-text documents.
  3. `acronym_or_variant_recoverable` — zero direct postings, but recoverable in Round 2 via an `ABBR (expansion)` or `expansion (ABBR)` pair mined directly from the case question or snapshot corpus.
  4. `corpus_absent` — zero postings across metadata, full text, and mined expansions. Log as an honest snapshot boundary; **never** state that no such literature exists globally.
- **Round 2 (Controlled Expansion & Marginal Gain)**:
  - Expand strictly from **corpus-mined abbreviations** (e.g., `OER <-> oxygen evolution reaction`, `PEM <-> proton exchange membrane`) and **co-occurring domain terms** (minimum support >= 2 documents in Round 1 neighborhood).
  - Record exact Round 1 -> Round 2 delta per sub-question (`r1_hits`, `r2_hits`, `marginal_new_hits`, `marginal_gain_rate`, `marginal_new_rids`) in `query_log.json`.
  - Compute an auditable **Stopping Verdict** (`STOP_SUFFICIENT_CONVERGED`, `STOP_DIMINISHING_RETURNS`, `STOP_FINITE_SNAPSHOT_COMPLETE`, or `STOP_WITH_GAPS_ESCALATE_HUMAN`).

### Phase C — Source Health Tri-Triage (PyAlex #103 & Malformed Snapshot Protection)
Every file under `/input` is triaged into one of three distinct health buckets—never collapsed into a single "0 hits" count:

| Bucket | Detection Signal | Required Pipeline Behavior |
|---|---|---|
| `source_load_errors` | Unparseable/truncated JSON (`ValueError`) or unsupported top-level JSON primitive (`unsupported_json_shape`) | Log file, exception type, and detail; set `result.json:status = "partial_with_gaps"`; emit `SOURCE_MALFORMED_OR_UNSUPPORTED_JSON` gap. |
| `pagination_issues` | Payload with `"status"` in `{error, failed, rate_limited, timeout, unavailable, truncated}` or non-empty `"error"` / invalid `"cursor"` (e.g., PyAlex #103 HTTP 400 on final page) | Log file, source, status, error, and cursor; set `status = "partial_with_gaps"`; emit `SOURCE_PAGINATION_OR_CURSOR_ERROR` gap. |
| `valid_empty_sources` | Cleanly parsed empty JSON array `[]` | Log `record_count: 0` with `classification: "valid_empty_snapshot"`; explicitly distinguish from errors so reviewers see healthy zero-hit queries vs broken snapshots. |

### Phase D — Traceable Deduplication, Version Chains & Anti-False-Merge Guard
1. **4-Tier Identity Matching**:
   - `T1`: Exact normalized DOI, PMID, or arXiv base ID.
   - `T2`: Preprint <-> Version-of-Record (VoR) upgrade via `alt_dois`, preprint DOI prefixes (`10.48550/arxiv.*`, `10.1101/*`, etc.), or high-similarity arXiv-to-DOI title+author links. Canonical record prefers `published > conference > preprint`.
   - `T3/T4`: High-similarity title + author/year alignment ONLY when records do NOT carry conflicting formal DOIs.
2. **Hard Distinct-Formal-DOI Guard**: If two records have `title_similarity >= 0.80` but carry **two distinct non-preprint DOIs** (e.g., parallel journal editions or unrelated studies sharing a title), keep them separate and append an entry to `ambiguous_candidates` (`action = "kept_separate_needs_human_arbitration"`).
3. **ASReview #328 Rollback Log & Conservation**: Never delete duplicates silently. Log every merged-away member in `rollback_log` with `duplicate_status = "duplicate_ignored_with_trace"`, `canonical_rid`, `match_tier`, `match_score`, and `rollback_action`. Verify `source_count_conserved == True` (`raw == unique + merged_away`).

### Phase E — Three-Perspective Annotation, GAP/Precedent Cues & 4-Tier Ranking
1. **Three-Perspective Evidence Tagging** (every candidate checked transparently against metadata cues; never guessed):
   - `method_developer`: synthesis, catalyst/algorithm design, DFT/theoretical modeling, mechanistic discovery, or protocol construction.
   - `frontline_application`: device/electrolyzer validation, durability/lifetime hours, dissolution/degradation under real operating conditions, reproducibility or failure limits.
   - `independent_review_eval`: reviews, surveys, perspectives, comparative benchmarks, or meta-analyses.
   - Records lacking explicit cues in metadata are labeled `unclassified_from_metadata`.
2. **GAP & Precedent Linkage**: Tag `gap_anchor`, `method_precedent`, `negative_or_limit_finding`, and `code_or_data_cue` (`mentioned_in_metadata` vs `not_reported_in_metadata`).
3. **Transparent 4-Tier Relevance Ranking**:
   - `core`: Direct multi-facet evidence within the time window satisfying the research question (`abstract_score > 0` or dual-reviewer agreement).
   - `method-only`: Relevant catalyst/method/mechanism studies lacking full operating-condition/application match, OR records missing abstracts (protected from silent exclusion via `missing_abstract_protected_from_silent_exclusion`).
   - `background`: Topically adjacent literature OR pre-window foundational papers (`year < year_min` with strong topical overlap) retained with explicit `PRE_WINDOW_CONTEXT` codes rather than silently dropped.
   - `excluded`: Hard rule exclusions, retracted works, or zero-overlap off-topic records with explicit `EXC:*` reason codes.
4. **Deterministic Tie-Breaking & LOO Rank Stability**:
   - Sort strictly by `(tier_order, -score, -abstract_score, -version_authority, -year, rid)`. Title-only keyword traps (`title_score >= 1.2` with `abstract_score == 0.0` when an abstract exists) are downgraded to prevent keyword stuffing from beating abstract-backed studies.
   - Compute Leave-One-Term-Out top-k overlap (`rank_stability`) to flag whether the top-10 ranking depends fragiley on a single query term.

---

## 4. Required Output Contract & Host Agent Verification Checklist
All outputs live under `/app/output/`:
- `result.json` — Must contain `case_id`, `status` (`"ok"` or `"partial_with_gaps"`), `results`, `evidence`, `status_report`, `source_load_errors`, `pagination_issues`, `valid_empty_sources`, `ambiguous_candidates`, `rollback_log`, `source_count_conserved`, `metrics`, `metric_provenance`, `findings`, `gaps`, `uncertainty`, and `artifacts`.
- `results_ranked.csv` — Flat reviewable table of all ranked canonical candidates with tiers, scores, perspectives, reason codes, and source locators.
- `evidence_chains.json` — Per-record evidence snippets, field-level matched terms, dedup provenance, version groups, and `rollback_log`.
- `query_log.json` — Element card, sub-question DAG, Round 1 & Round 2 adapted queries, mined abbreviations, `term_gap_diagnosis`, marginal gain table, and stopping verdict.
- `report.md` — Concise, audit-ready scientific summary following front-end/back-end separation (scientific findings and honest coverage limits first; technical diagnostics structured cleanly).
- `audit_log.json` & `run_manifest.json` — SHA-256 hash-chained execution log and file inventory.

**Before ending the task, the Host Agent must verify:**
1. Open `/app/output/report.md` and `/app/output/result.json` to confirm `source_count_conserved == True` and inspect top-10 `core` papers against the case question.
2. Confirm every item in `source_load_errors`, `pagination_issues`, `valid_empty_sources`, and `ambiguous_candidates` is surfaced transparently in `findings` and `gaps`—never hidden.
3. Confirm `gap_gate.py` (`ok: true`, zero ungrounded DOIs/metrics) and `final_audit.py` (`passed: true`) both exited `0`.
4. If the case includes local PDFs or specific manual review questions, cross-check `evidence_chains.json:fulltext_hits` and note any unverified full-text boundaries explicitly as `not_verifiable_from_metadata`.

---

## 5. DeepSeek Harness Inspection & Diagnostic Recipes
After running Step 1 and Step 2, use concise one-line inspections (never re-inventing the pipeline from scratch) to verify domain fidelity before drafting your final summary:

```bash
python3 -B -c "
import json
d = json.load(open('/app/output/result.json'))
print('STATUS:', d['status'], 'CONSERVED:', d['source_count_conserved'])
print('TIERS:', d['results']['tier_counts'])
print('R1->R2:', d['evidence']['round1_vs_round2']['round1_positive_hits'], '->', d['evidence']['round1_vs_round2']['round2_positive_hits'])
print('ERRORS:', len(d['source_load_errors']), 'PAGINATION:', len(d['pagination_issues']), 'VALID_EMPTY:', len(d['valid_empty_sources']))
print('AMBIGUOUS:', len(d['ambiguous_candidates']), 'LOO_STABILITY:', d['results']['rank_stability'])
for c in d['results']['ranked_candidates'][:5]:
    print(c['rank'], c['tier'], c['year'], c['score'], c['perspectives'], c['title'][:72])
"
```

### Query Drift Guard (Why Controlled Expansion Beats Blind Expansion)
Unconstrained synonym expansion on bibliographic corpora introduces severe topic drift (for example, expanding a single generic word such as `"oxide"` into `"graphene oxide (GO)"` or `"methanol oxidation reaction (MOR)"`). This Skill enforces a strict **question-anchor gate**:
1. Only mined abbreviations whose uppercase acronym appears in the research question (e.g., `OER`, `PEM`) OR whose expansion shares `>= 2` stemmed tokens with the question's key terms are admitted into Round 2 expansion.
2. Co-occurrence expansion requires document support `>= 3` inside the Round 1 neighborhood and excludes generic English/Chinese stopwords.
3. Every added expansion term is logged in `query_log.json:expansion_evidence` together with per-sub-question `marginal_new_hits` so reviewers can verify precision and recall separately.

### Decision Matrix for Edge Cases & Borderline Records
| Observed Condition | Automated Pipeline Action | Required Host Agent Reporting |
|---|---|---|
| Record has matching title (`>= 1.2`) but `abstract_score == 0.0` while abstract is present | Downgraded from `core` to `borderline`/`method-only` with `GUARD:title-only-no-abstract-evidence` | Explain that title keyword overlap was not corroborated by abstract evidence. |
| Record has missing abstract (`abstract == ""`) | Protected from silent exclusion (`missing_abstract_protected_from_silent_exclusion`) and routed to `method-only` | State explicitly that full text/abstract was absent from metadata and requires manual follow-up. |
| Record predates `year_min` (e.g., 2014 paper for a `since 2018` query) but has high topical score | Retained under `background` with `PRE_WINDOW_CONTEXT:year=<YYYY>` instead of silent drop | Distinguish foundational pre-window context from in-window `core` evidence. |
| Two records share an identical title (`sim == 1.0`) with two distinct formal DOIs | Blocked from auto-merge (`ambiguous:distinct_formal_dois`) and logged in `ambiguous_candidates` | Present both DOIs, venues, and authors for human arbitration (e.g., parallel editions vs distinct papers). |
| File contains truncated JSON or HTTP 400 cursor error (PyAlex #103) | Recorded in `source_load_errors` / `pagination_issues`; sets `status = "partial_with_gaps"` | Explicitly separate broken/truncated source pages from healthy `valid_empty_sources` (`[]`). |
| Query term has `0` postings in metadata and `0` in local full text | Classified as `corpus_absent` in `term_gap_diagnosis` | Report as a finite-snapshot boundary; never assert the concept does not exist in the broader literature. |

---

## 6. Failure Modes, Graceful Degradation & Pre-Exit Checklist
- **Empty or unreadable `/input`**: `result_guard.py` writes an early skeleton `result.json`, and `academic_search_pipeline.py` emits honest `status = "partial_with_gaps"` with exact `source_load_errors` instead of crashing silently.
- **Missing abstracts or missing publication years**: Protected from silent exclusion; routed to `method-only` / `borderline` with `MISSING:abstract` or `MISSING:year` codes.
- **Partial pagination / cursor failure**: Retained valid pages are still processed and ranked, while the broken page/cursor error is explicitly recorded in `pagination_issues` and `stopping_verdict = "STOP_WITH_GAPS_ESCALATE_HUMAN"`.
- **Budget discipline**: Within the 64-request / 900-second execution budget, run the deterministic script in turn 1, inspect `result.json` + `report.md` in turn 2, run M5+M6 audits in turn 3, and deliver your evidence-grounded synthesis cleanly.

### Pre-Exit Audit Verification Checklist
- [ ] `result.json` parses cleanly with zero `NaN`/`Infinity` values and `status` in `{"ok", "partial_with_gaps"}`.
- [ ] `source_count_conserved == True` (`raw_record_count == canonical_record_count + dedup_merged_count`).
- [ ] Every malformed JSON file appears in `source_load_errors` (never collapsed into 0 hits).
- [ ] Every cursor/HTTP error file appears in `pagination_issues` (never collapsed into 0 hits).
- [ ] Every cleanly parsed `[]` file appears in `valid_empty_sources`.
- [ ] Every same-title distinct-formal-DOI pair appears in `ambiguous_candidates` and remains unmerged.
- [ ] `query_log.json` records Round 1 vs Round 2 marginal gain per sub-question and `term_gap_diagnosis`.
- [ ] `evidence_chains.json` retains `rollback_log` (ASReview #328) and per-record source locators.
- [ ] `gap_gate.py` reports `ok: true` with `ungrounded_identifiers == []` and `ungrounded_metrics == []`.
- [ ] `final_audit.py` reports `passed: true` with `blocking_failures == []`.

### Additional References
- `references/search_protocol_and_rubric.md` — Detailed diagnostic rules, three-perspective cues, and 4-tier rubric.
- `references/output_contract_spec.md` — Full JSON and CSV field definitions.
- `examples/sample_input_case.json` & `examples/sample_output_schema_example.json` — Minimal illustrative interface examples.



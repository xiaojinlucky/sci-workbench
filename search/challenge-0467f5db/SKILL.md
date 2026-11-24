---
name: search-coverage-omission-auditor
description: Audit literature retrieval coverage against a frozen independent reference set without defining denominator from search outputs, attribute every missed paper to concrete query concept-block/field/date/source/failure causes, evaluate supplemental marginal net gains to stop zero-gain spinning, and emit human-verifiable coverage reports. Use when analyzing search coverage, recall gaps, or omission causes over finite retrieval snapshots.
---

# Search Coverage & Omission Analysis (`challenge-0467f5db`)

## 1. Mission & Non-Negotiable Invariants

This skill executes a closed-loop, independently recomputable coverage and omission audit (**Freeze Reference Set -> Identity-Safe Matching -> Actionable Omission Attribution -> Marginal Supplemental Gain Audit -> Verification Sampling**) over finite local retrieval snapshots (Crossref, EuropePMC, OpenAlex, arXiv XML, RIS, BibTeX, CSV, PDF, or structured JSON).

### Five Epistemic Invariants
1. **Independent Frozen Denominator**: Compute recall strictly against the frozen, deduplicated `reference_set`. Never define the denominator from search outputs; if `reference_set` is absent, emit `MISSING_REFERENCE_SET_DENOMINATOR` and `baseline_coverage_ratio: null`.
2. **Dual-Denominator Transparency**: Retain retracted, withdrawn, or unavailable reference items visibly rather than deleting them; report both **raw reference coverage** (`matched / total_frozen_refs`) and **actionable reference coverage** (`matched / actionable_refs`, excluding explicitly retracted/unavailable items).
3. **Identity-Safe Hit Alignment**: Exact normalized `DOI`, `PMID`, or `arXiv ID` matches are authoritative. Fuzzy matching requires ALL THREE: (a) token-sort title similarity `>= 88.0`, (b) both publication years present and `|year_ref - year_hit| <= 1`, and (c) matching normalized first-author surname. High-title-similarity candidates that fail author or year corroboration stay **unmatched** and are logged under `identifier_mismatch` with `candidate_match_evidence`.
4. **Concrete Query/Field/Source Attribution**: Never output generic reasons ("keywords not good enough" or "database incomplete"). Attribute every missed reference to an actionable class grounded in the exact `query_id`, missing Boolean concept block, field scope (`title` vs `title_abstract`), date window, source coverage, or explicit `source_unreachable_or_failed` status (timeout/403/auth failure must never be reported as "zero hits").
5. **Anti-Spinning Supplemental Verification**: Measure net-new recoveries strictly against previously missed reference items (`post_supplemental_coverage_ratio - baseline_coverage_ratio`). If supplemental queries return records that recover `0` new unique reference items, flag `supplemental_empty_spin_detected: true` and stop synonym spinning.

## 2. Information Hierarchy & References

Consult on demand (all paths relative to this `SKILL.md` directory):
- `references/output_contract_spec.md` — exact schema for `/app/output/result.json`, `coverage_audit_report.json`, and `coverage_audit_report.md`.
- `references/omission_taxonomy_and_stopping_rules.md` — 7-class omission decision tree, Boolean concept-block diagnostic rules, three-perspective coverage checklist, and stopping criteria.
- `references/search_methodology_protocol.md` — question decomposition, screening-not-cherry-picking principle, and finite-snapshot honesty rules.

## 3. Step-by-Step Execution Workflow

Run all commands from the directory containing this `SKILL.md` (or resolve `scripts/` relative to it). Never assume external network access.

### Step 1: Preflight Environment & Budget Initialization
```bash
python -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
```
**Completion criterion**: `/app/work/env.json` confirms readable `/input/case.json` (and any bibliographic/API/PDF snapshot files) and writable `/app/output`.

### Step 2: Execute Deterministic Coverage, Attribution & Marginal-Gain Pipeline
```bash
python -B scripts/coverage_omission_analyzer.py --input /input --output /app/output --work /app/work
```
What this engine deterministically performs:
1. **Multi-format ingestion (`corpus_loader.py`)**: Parses `case.json`, Crossref/EuropePMC/OpenAlex JSON (including OpenAlex `abstract_inverted_index` reconstruction), arXiv XML, RIS, BibTeX, CSV, and per-page PDF text without prohibited dependencies (`fitz`/`pymupdf` forbidden).
2. **Reference Freezing & Deduplication**: Deduplicates `reference_set` with SHA-256 fingerprint (`reference_set_sha256`), preserves `alt_dois`, separates `actionable_reference_count` from `retracted_or_unavailable_count`, and records `reference_dedup_removed`.
3. **Identity-Safe Matching**: Aligns baseline search hits against frozen references, retaining `source`, `source_file`, `query_id`, and `match_method` on every hit, and recording uncorroborated title collisions into `candidate_match_evidence`.
4. **Boolean Concept-Block & 7-Class Omission Attribution**: Parses query strings into `AND`-joined concept blocks (and `OR` synonym groups) and attributes each missed reference across 7 mutually exclusive priority classes:
   - `retracted_or_unavailable`
   - `source_unreachable_or_failed` (explicit source timeout/error/auth failure logged in input)
   - `date_window_cutoff`
   - `database_not_indexed`
   - `field_restriction_too_narrow` (terms present in abstract/fulltext but omitted by title-only query)
   - `identifier_mismatch` (title collision lacking corroborated author/year, or missing normalized identifier)
   - `synonym_gap` (with concrete `missing_concept_blocks[]` and candidate terms observed in the missed paper)
5. **Per-Query / Per-Source Contribution & Supplemental Marginal-Gain Curve**: Computes each query's and source's individual coverage and unique-only contribution, audits supplemental runs for genuine net-new recoveries vs zero-gain spinning, and emits targeted remediation query drafts grounded in missed-paper terminology.
6. **Verification Sampling & Dual Deliverables**: Generates a deterministic `verification_sample` (prioritizing fuzzy matches, identity collisions, and each omission class) for human inter-rater review, plus `/app/output/coverage_audit_report.json`, `/app/output/coverage_audit_report.md`, and `/app/output/result.json`.

**Completion criterion**: `coverage_audit_report.json`, `coverage_audit_report.md`, and `result.json` all exist with finite recomputable counts (`matched + missed == reference_denominator_count` and `baseline_matched + supplemental_net_new + residual_omissions == reference_denominator_count`).

### Step 3: Qualitative Three-Perspective & Pre-Mortem Check (Host Agent)
Inspect `/app/output/coverage_audit_report.json` and `/app/output/coverage_audit_report.md` directly before finalizing:
1. **Verify Identity Collisions**: Check every item in `missed_references_attributed` carrying `candidate_match_evidence`. Confirm it was NOT counted as a hit without author+year corroboration.
2. **Three-Perspective Blindspot Check**: Verify whether missed references disproportionately cluster in one perspective—(a) primary method/clinical developers, (b) applied practitioners/negative-result studies, or (c) independent synthesis/benchmark evaluations—and note any structural blindspot in `findings`.
3. **Stopping Discipline**: If `supplemental_empty_spin_detected` is `true` or marginal gain fell to `0`, document the exact residual omissions and stop further query spinning.
4. **Scope & Snapshot Honesty**: Confirm no statement extrapolates seed-set recall into live remote database coverage or global completeness.

### Step 4: Grounding Verification & Final Deliverable Gate
```bash
python -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python -B scripts/final_audit.py --dir /app/output --result-file result.json --require coverage_audit_report.json coverage_audit_report.md
```
**Completion criterion**: `final_audit.py` exits `0` with `passed: true`, and every metric in `result.json` traces via `metric_provenance` to an exact field in `coverage_audit_report.json`.

## 4. Failure Modes & Degraded-Input Handling

| Edge Case in `/input` | Required Behavior |
| --- | --- |
| Missing `reference_set` | Do NOT build denominator from search hits; set `reference_denominator_count: 0`, `baseline_coverage_ratio: null`, and emit `MISSING_REFERENCE_SET_DENOMINATOR`. |
| Unreachable/timed-out source in `source_statuses` or `source_failures` | Classify omissions tied to that source as `source_unreachable_or_failed` with raw error status; never report failed query as "0 hits". |
| Retracted / withdrawn / fulltext-unavailable seed | Keep in denominator; classify as `retracted_or_unavailable` (`evidence_state: OUTDATED` or `UNKNOWN`); report both raw and actionable coverage ratios. |
| Same title + year, different first author or missing author | Do NOT count as fuzzy hit; assign `identifier_mismatch` with full `candidate_match_evidence` (`record_id`, `source_file`, `query_id`, `identity_check`). |
| Supplemental queries return records already in baseline or irrelevant | Set `supplemental_net_new_hits_count: 0`, `supplemental_empty_spin_detected: true`, emit `SUPPLEMENTAL_SEARCH_ZERO_NET_GAIN`, and halt spinning. |

## 5. Hard Boundaries
- Report coverage strictly for the finite organizer-authorized snapshot; never claim live remote database recall or global completeness.
- Never fabricate missing references, author metadata, or positive supplemental gains.
- Keep `result.json` concise and machine-verifiable; never place internal debug states into scientific summaries.

---
name: systematic-review-pipeline
description: Reproducible 5-stage systematic literature review pipeline with SHA-256 frozen PICO/PECO search protocol, non-destructive multi-source deduplication, clause-priority two-stage screening (Stage 1 title/abstract only, Stage 2 full-text only with PRISMA 2020 Item 16a not-retrieved separation and dual-reviewer disagreement queue), versioned 5-domain quality/risk-of-bias appraisal, and opposing-evidence-aware synthesis.
---

# Systematic Literature Review (`systematic-review-pipeline`)

A systematic literature review must be independently reproducible from start to finish: a second researcher given the same frozen protocol, search strategy, and corpus must be able to recompute the exact PRISMA 2020 counts, audit every exclusion clause, verify every quality appraisal domain, and trace every synthesis claim back to its source locator.

## Five Auditable Stages & Hard Guardrails

1. **Stage 1 — Frozen PICO/PECO Protocol & Search Strategy Lock (`protocol_sha256`)**
   - Structure the research question into `population_or_object`, `intervention_or_exposure`, `comparator`, `outcome`, `time_window`, and `languages`.
   - Record executable boolean queries, target databases, searched fields, and clause priority order (`EXC_OUT_OF_YEAR_WINDOW` > `EXC_LANGUAGE_RESTRICTION` > `EXC_STUDY_TYPE` > `EXC_EXCLUSION_CLAUSE` > `EXC_MISSING_REQUIRED_CONCEPT` > `NOT_RETRIEVED_FULLTEXT_REQUIRED` > `EXC_FULLTEXT_EXCLUSION_TERM`).
   - Freeze the protocol with `protocol_sha256` before screening begins — never silently relax or drift eligibility clauses mid-screening.

2. **Stage 2 — Non-Destructive Deduplication & Preprint-to-VoR Lineage**
   - Merge exact DOI, arXiv ID, PMCID, PMID, and conservative title+author duplicates while preserving `deduplication_log`, `alt_dois`, `found_in_sources`, and `version_upgrade` (`preprint_to_vor_merged`).
   - Never silently merge two records with distinct non-preprint DOIs; log borderline title similarities in `metadata_conflicts` for human check.

3. **Stage 3 — Strict Two-Stage Screening & PRISMA 2020 Item 16a Conservation**
   - **Stage 1 (Title/Abstract ONLY)**: Evaluate `year_min/max`, language, study type, `exclude_terms`, and `required_terms` strictly on Title + Abstract — never peek into full text during Stage 1.
   - **Stage 2 (Full-Text ONLY)**: Evaluate retrieved full text against `exclude_terms + fulltext_exclude_terms` and record `stage2_reason_code` + `stage2_exclusion_reason` for every exclusion.
   - **PRISMA 2020 Item 16a (`not_retrieved` separation)**: When `protocol.fulltext_required: true`, Stage-1 eligible records without retrieved full text are assigned `stage2_fulltext="not_retrieved"` (`NOT_RETRIEVED_FULLTEXT_REQUIRED`). Count them in `n_sought` and `n_not_retrieved`, **NEVER** in `n_elig_excl` or `n_included`.
   - **Conservation Invariants**:
     - `n_identified == sum(identified_by_source.values()) == n_dedup + n_screened`
     - `n_sought == n_screened - n_screen_excl`
     - `n_assessed_for_eligibility == n_sought - n_not_retrieved`
     - `n_assessed_for_eligibility == n_elig_excl + n_awaiting_elig + n_included`
     - `sum(fulltext_exclusion_reason_counts.values()) == n_elig_excl`
   - Flag ambiguous or clause-conflicting records in `dual_reviewer_disagreement_queue` rather than hiding ambiguity.

4. **Stage 4 — Negation-Guarded 5-Domain Quality & Risk-of-Bias Appraisal (`SLR-Appraisal-v3-NegationGuarded`)**
   - Appraise every included study transparently across 5 explicit domains with exact `#page=N` / `#sec=...` locators and rationales:
     - `D1_study_design_and_protocol`
     - `D2_sample_and_inference_unit`
     - `D3_comparator_and_controls`
     - `D4_bias_confounding_and_transparency`
     - `D5_fulltext_access_and_verifiability`
   - **Negation & Secondary-Review Guardrails**: Negative methodological cues (e.g., *"no control group"*, *"without randomization"*, *"severe confounding"*, *"high risk of bias"*) are graded `HIGH_RISK` — **NEVER** `LOW_RISK` merely because keywords appear. Secondary reviews/commentaries are flagged `SECONDARY_REVIEW_NOT_PRIMARY_TRIAL` rather than inflated to `HIGH` primary-trial quality.
   - **Anti-Fabrication Rule**: Unreadable or unreported domains MUST be tagged `NOT_ASSESSABLE_FROM_ABSTRACT` (for abstract-only inclusions) or `NO_INFORMATION` — **NEVER** defaulted to `LOW_RISK`.
   - Report how overall quality tiers (`HIGH` / `MODERATE` / `LOW` / `INCOMPLETE_ABSTRACT_ONLY` / `SECONDARY_REVIEW_NOT_PRIMARY_TRIAL`) bound the synthesis in `sensitivity_impact_summary`.

5. **Stage 5 — True Opposing-Evidence Binding & Strict Read-Only Report Verification**
   - Bind `opposing_evidence[]` strictly to verbatim `opposing_or_null` / `mixed` passages (never positive abstract lead sentences) with exact locators and quality tiers.
   - Separate prospective protocols from completed trials; never assert global completeness from a bounded snapshot.

## Execution Workflow

All script paths below are relative to the directory containing this `SKILL.md` (resolve `scripts/...` against the installed skill root).

### Step 1: Preflight & 5-Stage Systematic Review Execution
```bash
python3 scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 scripts/systematic_review_runner.py \
  --input /input \
  --output /app/output \
  --work /app/work
```
Outputs `/app/output/systematic_review_bundle.json`, `/app/output/systematic_review_report.md`, and `/app/output/result.json`. Screening applies word-boundary (`\b...\b`) matching, automatically invokes `block_indexer.py` on local files, and normalizes multi-source provenance labels (`openalex`, `crossref`, `europepmc`, `europepmc_preprint`, `arxiv`, etc.).

### Step 2: Narrative Synthesis & Protocol Audit (Agent Reasoning Pass)
Inspect `systematic_review_bundle.json` and refine `/app/output/systematic_review_report.md` using `references/prisma_and_appraisal_protocol.md`:
- Confirm `prisma_flow.balanced == true` and every count matches the row-level `screening_ledger`.
- Review `dual_reviewer_disagreement_queue` and document adjudication rationales without altering the frozen `protocol_sha256`.
- Anchor primary synthesis conclusions on `HIGH`/`MODERATE` primary full-text studies, explicitly qualifying `INCOMPLETE_ABSTRACT_ONLY` or `SECONDARY_REVIEW_NOT_PRIMARY_TRIAL` records and preserving genuine opposing results side by side.

### Step 3: Read-Only Report Audit, Grounding & Final Deliverable Check
```bash
python3 scripts/systematic_review_runner.py \
  --input /input \
  --output /app/output \
  --work /app/work \
  --verify-report /app/output/systematic_review_report.md \
  --out-json /app/work/report_audit.json \
  --strict
python3 scripts/gap_gate.py \
  --verify-json /app/output/result.json \
  --corpus /input \
  --gaps-out /app/work/gaps.json
python3 scripts/final_audit.py \
  --dir /app/output \
  --result-file result.json \
  --require systematic_review_bundle.json \
  --require systematic_review_report.md
```
`--verify-report --strict` runs strictly read-only (never overwrites `systematic_review_report.md` or `result.json`), verifying that reported PRISMA counts match the conserved bundle, all cited `[...]` IDs are grounded and non-excluded, and no overclaims appear.

## Exception Handling
- **Missing full text (`fulltext_required: true`)**: Logged in `n_not_retrieved` and `gaps` (`FULLTEXT_NOT_RETRIEVED`), never misclassified as excluded or included.
- **Abstract-only compatibility (`fulltext_required` absent/false)**: Included with explicit `included_abstract_only_caveat`, graded `INCOMPLETE_ABSTRACT_ONLY` / `PARTIAL`, and flagged in `gaps`.
- **Pagination truncation or blocked sources**: Disclosed in `uncertainty` and `gaps`; never reported as exhaustive retrieval.

## References & Examples
- `references/output_contract_spec.md` — PRISMA 2020 equations and required output fields.
- `references/prisma_and_appraisal_protocol.md` — Clause-priority rules and 5-domain quality appraisal rubric.
- `examples/minimal_example.md` — Minimal illustrative example.

---
name: literature-screening-ranker
description: Perform auditable all-record literature screening and intelligent ranking over finite candidate sets using 4-tier relevance labels (direct_relevant/indirect_relevant/insufficient_abstract_needs_review/irrelevant), title-only trap protection, missing-abstract/year safeguards, traceable duplicate ignoring with rollback logs, external conflicting-opinion arbitration, deterministic 7-level tie-breaking, Cohen's kappa, and leave-one-term-out rank stability. Use for literature screening and smart ranking tasks.
---

# Literature Screening & Intelligent Ranking (`challenge-2dd1f64c`)

## 1. Purpose, Scope & Hard Invariants

This Skill eliminates opaque "black-box score" rankings and silent record loss when screening noisy candidate sets.

**In Scope**:
- Complete all-record screening against stated research question, criteria, and filters—covering 100% of candidate entries with PRISMA count conservation (`identified == unique + traceable_duplicates`, `unique == include + borderline + exclude`).
- Explicit 4-tier relevance taxonomy (`direct_relevant`, `indirect_relevant`, `insufficient_abstract_needs_review`, `irrelevant`) + evidence role tagging (`core_evidence`, `method_reference`, `background_review`, `background_context`, `needs_abstract_or_fulltext`, `excluded`).
- Anti-title-stuffing safeguard: balances abstract evidence weight (`2.2`) against title weight (`2.4`) and detects `title_only_trap` when a paper hits keywords in its title while the abstract reports an unrelated domain/population.
- Missing-metadata protection: candidates lacking abstracts or publication years are routed to `borderline` (`insufficient_abstract_needs_review` / `MISSING:year`) and NEVER silently excluded.
- Traceable duplicate & version handling: duplicates and preprint/VoR variants are logged as `duplicate_ignored_with_trace` with explicit `rollback_log` entries (never silently dropped); distinct formal DOIs with identical titles stay separate in `ambiguous_candidates`.
- Conflict arbitration & human-in-the-loop priority: automated ranking assists triage and NEVER overrides hard exclusion rules or conflicting reviewer opinions; conflicts go to `human_review_queue.json`.
- Transparent, reproducible priority ordering with an explicit 7-level deterministic `tie_breaker_chain`, explicit `k_evaluated`, and leave-one-term-out (LOO) top-k rank stability.

**Out of Scope & Hard Boundaries**:
1. Finite local snapshot only: no live database connections, no global recall claims, no fabricated human review approvals.
2. Synthetic duplicate exports or conflicting opinion fixtures (e.g., `search09`) are input fixtures to screen and arbitrate—never pre-baked answers.
3. Every retain/borderline/exclude judgment MUST cite matched criteria rules, abstract snippet, field scores, confidence level, and source file pointer.

## 2. Deterministic Execution Workflow

All scripts live under `scripts/` relative to this `SKILL.md` and run on pure Python 3.12 standard library (`--network none`).

### Step 1: Preflight & Budget Initialization (`M1` + `M3`)
```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 -B scripts/budget_guard.py --state /app/work/.budget_state.json --tick screen_and_rank
```

### Step 2: Execute All-Record Screening, Dedup Tracing & Intelligent Ranking
```bash
python3 -B scripts/smart_screening_ranker.py --input /input --output /app/output --work /app/work
```
This produces:
- `/app/output/screened_ranked.csv` (full ordered candidate table with decision, 4-tier relevance, evidence role, score breakdown, arbitration, and reason codes)
- `/app/output/screening_audit.json` (scoring weights, 7-level tie-breaker chain, PRISMA flow conservation, Cohen's kappa, LOO rank stability, duplicate trace/rollback log, and per-record evidence chains)
- `/app/output/human_review_queue.json` (prioritized triage list of borderline, missing-abstract, title-trap, conflicting-opinion, and ambiguous-identity items)
- `/app/output/report.md` (auditable screening & ranking brief)
- `/app/output/audit_log.json`, `/app/output/run_manifest.json`, and `/app/output/result.json`

### Step 3: Agent Semantic Calibration & Conflict Review

Before finalizing, inspect `/app/output/screening_audit.json`, `/app/output/human_review_queue.json`, and top-15 rows of `/app/output/screened_ranked.csv`:

1. **Title-Only Trap & Domain-Mismatch Verification**:
   - Check top-ranked `include` records to ensure none match keywords purely in the title while targeting the wrong species, population, or study design in the abstract. If a subtle semantic mismatch is spotted beyond lexical rules, note it explicitly in the review rationale and downgrade to `borderline` or `exclude` with quoted abstract evidence.
2. **Missing-Metadata & Conflict Queue Verification**:
   - Confirm that records with empty abstracts or missing years were NOT silently excluded (`metrics.missing_abstract_protected`) and that any external conflicting reviewer opinions (e.g. R1 `include` vs R2 `exclude`) remain escalated in `human_review_queue.json`.
3. **Duplicate Trace & Conservation Audit**:
   - Verify `metrics.prisma_flow.consistency == true` and `metrics.source_count_conserved == true`. Confirm preprint/VoR pairs link their versions cleanly while distinct formal DOIs with identical titles stay unmerged in `ambiguous_candidates`.
4. **Tie-Breaking & Stability Check**:
   - Check `metrics.ties_resolved_deterministically` and `metrics.rank_stability`. If LOO stability is `unstable` (`< 0.70`), explicitly flag in `report.md` that top-k ordering is sensitive to a single query term.

### Step 4: Grounding & Final Audit (`M5` + `M6`)
```bash
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json --require screening_audit.json --require screened_ranked.csv --require human_review_queue.json --require report.md
```

## 3. Distinction Between Internal Consistency & External Gold Agreement

- `metrics.internal_dual_signal_kappa`: Measures agreement between independent lexical+rule screening (Signal A) and facet+evidence+abstract screening (Signal B). High internal agreement signals deterministic consistency, NOT external human validation.
- `metrics.external_gold_agreement`: Computed automatically ONLY when `/input/` supplies an explicit external gold/reference label file (`*gold*`, `*ground_truth*`, `*reference_labels*`, `*annotated*`). When absent, the script explicitly reports `not_applicable_no_gold_labels_in_input` and never fabricates precision/recall/F1.
- **Anti-Overfitting & Cross-Domain Generalization**: The screening engine relies on general bibliographic structure (field-weighted BM25, multi-term exclusion co-occurrence guards, abstract-presence checks, DOI/arXiv identity tiers, and 7-level deterministic tie-breaking) rather than hardcoded single-domain keywords, ensuring stable triage across electrocatalysis, biomedicine, and machine learning corpora.

## 4. Exception Handling & Edge Cases

| Edge Case | Required Behavior |
| --- | --- |
| **Empty candidate set** | Return valid `result.json` with `results: []`, `status.not_completed` noting 0 records, no fabricated scores. |
| **Missing abstract or year** | Route to `borderline` (`insufficient_abstract_needs_review` / `MISSING:year`), never silently exclude. |
| **Title matches, abstract unrelated** | Flag `title_only_trap: true`, `GUARD:title-only-no-abstract-evidence`, block automatic `include`. |
| **Conflicting criteria or reviewer opinions** | Escalate to `human_review_queue.json` with all raw opinions preserved; tag `CONFLICT:needs-human-arbitration`. |
| **Exact score ties** | Resolve strictly via the 7-level deterministic `tie_breaker_chain` and attach `tie_break_key` to each tied record. |
| **Cross-source duplicates & preprints** | Record merged duplicates as `duplicate_ignored_with_trace` in `duplicate_trace_log` with rollback action; preserve `version_group`. |
| **Malformed source JSON** | Record in `status.source_load_errors` (`SOURCE_SNAPSHOT_LOAD_FAILED`); keep valid `[]` separate. |

## 4. References & Examples

- Screening taxonomy, tie-breaking rules, and arbitration protocol: [references/screening_and_ranking_rubric.md](references/screening_and_ranking_rubric.md)
- Output contract schema: [references/output_contract_spec.md](references/output_contract_spec.md)
- Illustrative examples (not formal case answers): [examples/sample_input_case.json](examples/sample_input_case.json), [examples/sample_output_schema_example.json](examples/sample_output_schema_example.json)

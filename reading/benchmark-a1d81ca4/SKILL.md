---
name: literature-benchmark-harness
description: Audit source-conditioned reading benchmark workorders for card quality and answer leakage, compute deterministic 0/1/2 multi-dimensional scores with six-class failure taxonomy and repetition stability metrics, draft targeted regression items, and track comparability-aware version diffs with incremental recompute lists.
---

# Literature Reading Benchmark & Evaluation Harness (`benchmark-a1d81ca4`)

## Purpose & Trigger
Use this skill for scientific literature-reading benchmark authoring, auditing, deterministic evaluation, failure-case analysis, and version diffing under Organizer Revision 2 (`benchmark-a1d81ca4`):
- Operates on four-item (or N-item) source-conditioned `benchmark-DRAFT` workorders whose provided sources are **paragraph/table text (`paragraph_and_table_text_only_not_pdf_pixel_gold`)**, not PDF-pixel gold.
- Explicitly separates **local deterministic/synthetic rule checks** from official DeepSeek Harness (DSH) scores and independent human semantic validation.
- Preserves original scientific source bytes and item SHA-256 fingerprints without silent wording edits.

## Hard Invariants & Boundaries
1. **Honest Evaluation Scope**: Always emit `score_disclaimer` stating that local deterministic rule checks and synthetic checker logs are **NOT** official DSH scores or independent semantic human validation.
2. **Strict Source Anchoring**: For every structured evidence object `{paragraph_id, quote}`, `paragraph_id` must resolve to an actual supplied source block and `quote` must match verbatim inside **that exact block** (zero cross-paragraph borrowing).
3. **Inline vs Structured Citation Consistency**: Compare inline citation markers in the answer text (e.g., `[1]`, `[P001]`, `(Table 1)`) against the structured `evidence` array count; flag mismatches as `citation_count_mismatch`.
4. **Numeric vs Unit Separation**: Evaluate numeric equivalence with configurable relative tolerance and SI/scientific unit normalization so `unit_mismatch` is diagnosed separately from `numeric_value_mismatch`.
5. **Comparability-Aware Version Diff**: Compute dual SHA-256 hashes per item (`semantic_sha256` on scoring-critical fields vs `full_sha256` on the full card) so harmless metadata edits do not falsely invalidate historical comparability, while breaking changes generate an exact `incremental_recompute_ids` subset.

## Execution Workflow
Locate all scripts relative to this `SKILL.md` directory (`scripts/`).

### Step 1: Preflight & Item-Bank Pre-Run Audit
```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B scripts/budget_guard.py --init --state /app/work/.budget_state.json
python3 -B scripts/item_bank_auditor.py --input /input --out /app/work/item_audit.json
```
Checks required item fields, `data_license`/version metadata, duplicate `item_id`s, corpus existence of every `required_paragraph_ids`, empty `reference_answer` on answerable items, and verbatim answer-in-prompt leakage (`PROMPT_ANSWER_LEAKAGE`).

### Step 2: Run Deterministic Benchmark Harness
```bash
python3 -B scripts/benchmark_runner.py --input /input --output /app/output --work /app/work
```
Produces three synchronized deliverables under `/app/output/`:
- `result.json` — Harbor contract JSON (`case_id`, `status`, `sources`, `uncertainty`, `artifacts`, `metrics`, `score_disclaimer`, `item_bank_audit`, `scoreboards`, `stability_summary`, `failure_taxonomy_bank`, `regression_item_drafts`, `version_diff`, `gaps`).
- `benchmark_panel.json` — Complete machine-readable evaluation panel with per-item 0/1/2 diagnostics, per-agent multi-run variance/flip rates, budget/latency audit, and incremental recompute plan.
- `benchmark_report.md` — Human-readable audit & evaluation report summarizing item readiness, score breakdown by task type, six-class failure taxonomy, regression item drafts, and version comparability.

### Step 3: Semantic Inspection & Grounding Gate
1. Review any `PROMPT_ANSWER_LEAKAGE` or `MISSING_CORPUS_PARAGRAPH` warnings in `benchmark_report.md`.
2. Inspect `failure_taxonomy_bank` and `regression_item_drafts` for edge cases needing human semantic confirmation.
3. Run Harbor grounding and final deliverable audit:
```bash
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json \
  --require benchmark_panel.json --require benchmark_report.md
```

## References
- `references/item_card_and_leakage_audit.md` — Item card schema, authorization fields, dual hashing, and leakage detection rules.
- `references/scoring_012_and_failure_taxonomy.md` — 0/1/2 scoring matrix, numeric/unit separation, citation-count parity, and six-class failure taxonomy.
- `references/version_snapshot_and_incremental_recompute.md` — Snapshot diff rules, breaking vs metadata-only edits, and selective recomputation protocol.

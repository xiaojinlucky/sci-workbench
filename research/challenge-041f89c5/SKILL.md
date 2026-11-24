---
name: topic-novelty-feasibility
description: Decision-grade research topic formulation, prior-work comparison matrix builder, 4-axis novelty red-teamer, source-verified 5-axis feasibility auditor ([GROUNDED]/[ASSUMPTION]/[BLOCKED_UNKNOWN]), and 5-column Pre-Mortem risk engine strictly bounded to inspected local sources.
---

# Research Topic Selection & Novelty Evaluation (`topic-novelty-feasibility`)

Before committing experimental, computational, or grant resources, a researcher must answer four tightly coupled questions: Is the question falsifiable? How far has prior work actually progressed? Where is the genuine increment vs. existing overlap? And do the real prerequisites hold? Smooth, generic "technically feasible / first-of-its-kind" prose cannot support real research decisions.

## Five Core Evaluation Pillars

1. **6-Slot Falsifiable Research Question Formulation (With Vacuous-Value Blocker)**
   - Every proposed topic must specify: `target_object`, `key_variables`, `baselines`, `expected_outcome`, explicit **`falsification_criteria`** (what empirical outcome would *disprove* the hypothesis), and `inference_unit_and_scope_boundary` (e.g., independent biological replicates vs. cells/runs).
   - Placeholder or vacuous values (`"None"`, `"TBD"`, `"Best ever"`, `<10` characters) fail strict validation automatically. Never treat an existing paper's empirical conclusion as a predetermined outcome of the proposed project; use prior papers to map known space and locate gaps.

2. **Per-Paper Prior-Work Comparison Matrix & Incomparability Disclosure**
   - Parse every multi-source prior work (OpenAlex, Crossref, EuropePMC JSON/XML, arXiv XML, PDF, CSV, MD) individually into a structured matrix using domain-general syntactic extraction: task/object, **actual functional methods** (not just tool labels), evaluation metrics, key finding locator, and reported limitations.
   - Explicitly declare `direct_comparability_status` (`directly_comparable_fulltext`, `incomparable_abstract_only`, `incomparable_different_evaluation_metrics`, `partially_comparable_different_method_family`) — never compare incompatible benchmarks or abstract-only claims as head-to-head baselines.

3. **4-Axis Novelty Decomposition & Unified-Space Red-Team Overlap Audit**
   - Classify every novelty claim strictly into one of four mutually distinct categories:
     - `method_innovation` (genuinely new algorithm/assay/mechanism)
     - `data_innovation` (new cohort/modality/measurement scale)
     - `scenario_transfer` (applying an established method to a new domain/population — **never inflate into theoretical breakthrough!** Claims that label domain transfer as `method_innovation` without genuine algorithmic/mechanistic novelty cues fail strict validation as `SCENARIO_TRANSFER_MISLABELED`.)
     - `conclusion_correction` (resolving an opposing conflict or falsifying a prior assumption)
   - Score every claim against the **Top-3 closest local neighbors** inside ONE unified TF-IDF vector space (lexical + cosine + method overlap). Emit ONLY bounded verdicts (`CANDIDATE_NOVEL_WITHIN_CORPUS` / `CANDIDATE_NOVEL_ABSTRACT_BOUNDED` / `PARTIALLY_COVERED_INCREMENTAL` / `PARTIALLY_COVERED_ABSTRACT_BOUNDED` / `COVERED_BY_PRIOR_WORK` / `COVERED_BY_PRIOR_WORK_ABSTRACT_BOUNDED` / `SCENARIO_TRANSFER_MISLABELED` / `UNVERIFIED_INSUFFICIENT_FULLTEXT`). When Top-3 neighbors are `abstract_only`, output a decision-grade `*_ABSTRACT_BOUNDED` verdict paired with explicit `fulltext_verification_targets[]` naming exact neighbors and required Methods/Baseline sections rather than dead-ending at unverified; reserve `UNVERIFIED_INSUFFICIENT_FULLTEXT` for zero-abstract/discovery-only neighbors. Never equate "not retrieved" with "globally novel".

4. **Exact Source-Verified 5-Axis Feasibility Audit (`[GROUNDED]` / `[ASSUMPTION]` / `[BLOCKED_UNKNOWN]`)**
   - Evaluate all five prerequisite axes: `data_availability`, `compute_budget`, `experimental_conditions`, `timeline_window`, and `team_capability`.
   - Every `[GROUNDED]` premise MUST cite a real `source_ref` matching an exact normalized file path, filename, or record/DOI/arXiv/PMCID identifier present in `/input` (`case.json`, `task.md`, or verified local corpus record — generic substrings such as `"arxiv"` or `".json"` are strictly rejected).
   - Unknown costs, data access, or ethics/biosafety approvals MUST be tagged `[BLOCKED_UNKNOWN]` (never assumed zero friction!) and drive an explicit conditional decision (`BLOCKED_PENDING_PREREQUISITES`, `FEASIBLE_WITH_CONDITIONS`, or `CONDITIONALLY_READY_FOR_PILOT`).

5. **Opposing Evidence & 5-Column Pre-Mortem Risk Matrix**
   - Preserve genuine negative, null, and limiting prior passages (excluding PDF mastheads/author headers) in `opposing_evidence[]` so the proposal is never evaluated on cherry-picked supportive citations.
   - Run a **Pre-Mortem** assuming the project fails or is rejected, outputting: `priority`, `failure_mode`, `earliest_warning_signal`, `preventive_action`, `stop_or_pivot_trigger`, and `contingency_plan`.

## Execution Workflow

All script paths below are relative to the directory containing this `SKILL.md` (resolve `scripts/...` against the installed skill root).

### Step 1: Probe Environment & Run Deterministic Topic Evaluation
```bash
python3 scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 scripts/novelty_feasibility_evaluator.py \
  --input /input \
  --output /app/output \
  --work /app/work
```
Outputs `/app/output/topic_evaluation.json`, `/app/output/topic_comparison_matrix.md`, and `/app/output/result.json`. Automatically invokes `block_indexer.py` on local files and auto-extracts the proposal directly from `/input/case.json` and `/input/task.md` when `--eval-json` is omitted.

### Step 2: Deep Red-Team Refinement & Strict Spec Audit (Agent Reasoning Pass)
Inspect `topic_evaluation.json` and, when refining the structured evaluation, write `/app/work/topic_eval_spec.json` (containing refined `question_formulation`, `novelty_claims[]`, `feasibility_premises[]`, and `opposing_evidence[]`) and audit strictly:
```bash
python3 scripts/novelty_feasibility_evaluator.py \
  --input /input \
  --eval-json /app/work/topic_eval_spec.json \
  --output /app/output \
  --work /app/work \
  --strict
```
To run a non-destructive JSON-only check on a candidate spec without modifying `/app/output/topic_comparison_matrix.md` or `result.json`, pass `--out-json /app/work/spec_check.json --strict` (omitting `--output` and `--out-md`).

Consult `references/novelty_and_premortem_protocol.md` to ensure:
- Every high-overlap novelty claim either carries a concrete, source-anchored `differentiation_note` or is demoted to `scenario_transfer` / `PARTIALLY_COVERED_INCREMENTAL`.
- Every `[GROUNDED]` feasibility premise points to an exact verified local `source_ref`; every missing premise is honestly tagged `[ASSUMPTION]` or `[BLOCKED_UNKNOWN]`.
- The Pre-Mortem matrix identifies the sharpest kill-switch experiment / pilot check before full commitment.

### Step 3: Provenance Grounding & Final Deliverable Audit
```bash
python3 scripts/gap_gate.py \
  --verify-json /app/output/result.json \
  --corpus /input \
  --gaps-out /app/work/gaps.json
python3 scripts/final_audit.py \
  --dir /app/output \
  --result-file result.json \
  --require topic_evaluation.json \
  --require topic_comparison_matrix.md
```

## References & Examples
- `references/output_contract_spec.md` — Required output fields and provenance contract.
- `references/novelty_and_premortem_protocol.md` — 3-dimension topic alignment, top-journal gap ruler, and Pre-Mortem audit checklist.
- `examples/minimal_example.md` — Minimal illustrative walkthrough.

---
name: aligned-plan-comparator
description: >-
  Source-grounded horizontal comparator and selector for 3+ versioned
  experimental plans under organizer prospective resource, pricing, and booking
  contracts. Enforces unified comparable field matrices, strict null-vs-zero
  cost and approval firewalls, DAG schedule normalization, estimability checks,
  Pareto dominance, multi-weight rank stability, and explicit drop rationales
  into /app/output.
---

# Aligned Experimental Plan Comparator & Selector (`challenge-7be76e91`)

Place **3 or more versioned candidate experimental plans (`v1`, `v2`, `v3`)** onto a single unified, source-anchored comparison matrix covering **objective fit, controls & estimability, normalized total cost, normalized wall-clock schedule, finite parameter compliance, risk triggers, and evidence strength**, then produce a defensible selection (or honest `all_infeasible` / `unresolved` gate) with exact drop rationales and sensitivity boundaries.

## Runtime & Budget Contract

- **Skill root**: `/skills/weekly-skill` (scripts under `/skills/weekly-skill/scripts`, references under `/skills/weekly-skill/references`).
- **Inputs**: Inspect `/input/case.json`, `/input/task.md`, candidate plan documents, pricing/booking contracts, and scientific references under `/input/`.
- **Working directory**: `/app/work` (`env.json`, `.budget_state.json`, `input_blocks.json`, `parsed_plans.json`, `aligned_matrix.json`, `gaps.json`).
- **Deliverables**: `/app/output/plan_comparison.json`, `/app/output/plan_comparison.md` (plus any exact filename required by `/input/case.json`), and `/app/output/result.json` preserving `case_id` and every `output_contract` field.
- **Execution budget**: 64 requests / 900 s / 16,384 output tokens per turn. Initialize `preflight_probe.py`, `budget_guard.py`, and `result_guard.py` on Turn 1.

## Five-Step Horizontal Comparison & Selection Protocol

### Step 1: Unified Comparable Schema & Estimability Audit (`aligned_matrix_normalizer.py`)
- Map every candidate plan onto identical structured columns — NEVER compare narrative paragraphs written in asymmetric schemas.
- Check **Cross-Plan Comparability / Estimability**:
  - Verify whether candidates target the same `primary_readout`, `inference_unit`, and comparable control baseline.
  - If a candidate omits a control block or uses an unaligned readout, record it under `incomparable_or_missing_dimensions` so hidden apples-to-oranges comparisons are exposed.
- Track `evidence_provenance_pct` (fraction of populated comparison cells anchored to `source_ref`).

### Step 2: Strict Null-vs-Zero Cost, Approval & Schedule Normalization
- **Full Cost Accounting**: Sum `one_time_setup_cost + consumables + machine_hours + labor + rework_reserve`.
- **Full Wall-Clock Schedule**: Compute DAG critical path over `steps[].depends_on` (or parallel step buckets) incorporating `prep_days`, reagent `lead_days`, instrument `booking_lead_days`, assay `duration_days`, and `qc_turnaround_days`.
- **Null-vs-Zero Firewall (Zero Coercion)**:
  - Explicit `0` (`one_time_setup_cost: 0`, `unit_cost: 0`, `lead_days: 0`) is preserved as `verified_zero`.
  - Omitted or `null` cost rates, setup fees, labor days, lead times, or required safety/ethics approvals stay `null` / `UNSPECIFIED` and mark `cost_comparable: false` or `approval_status: UNVERIFIED` — NEVER filled with `0` to give an incomplete plan an artificial cost advantage.

### Step 3: Four-Bucket Disposition & Hard-Constraint Pruning (`plan_choice_evaluator.py`)
Partition all candidate plans into four mutually exclusive, transparent disposition buckets:
1. `selected`: Highest-ranked feasible, fully verifiable plan on the Pareto frontier.
2. `feasible_alternatives`: Feasible, verifiable runner-up plans with explicit quantitative trade-off deltas vs the selected plan.
3. `rejected_hard_constraints`: Plans violating confirmed hard caps (`budget_cap`, `deadline_days`, `unavailable` resource, out-of-range/`safety_max` parameter, denied approval, or schedule cycle) with exact numeric violation citations.
4. `unresolved_incomplete_evidence`: Plans with zero confirmed hard violations but missing cost rates, missing schedule leads, unverified approvals, or missing fit/evidence scores. They are **kept out of forced ranking** until missing evidence is supplied.

### Step 4: Pareto Dominance & Multi-Profile Sensitivity Analysis
- Identify the **Pareto non-dominated frontier** across `(scientific_fit, evidence_strength, -total_usd, -total_days, -risk_penalty)` and list explicit dominance pairs (`v2 dominates v1`).
- Run deterministic **5-Profile Weight-Perturbation Sensitivity** (`balanced`, `scientific_first`, `cost_first`, `speed_first`, `risk_averse`) to report `rank_stability_rate` (e.g., `5/5 profiles select v2`) and exact **1-factor budget/deadline flip thresholds** (`budget_slack_usd`, `deadline_slack_days`).

### Step 5: Defensible Choice, Drop Rationales & All-Infeasible Remediation
- Every non-selected plan MUST carry a concrete, evidence-anchored drop rationale (never vague prose).
- If `all_infeasible: true`, do NOT force a winner: report the exact binding constraints across all candidates and emit minimal relaxation or hybrid-plan remediation options.
- Include a Pre-mortem fallback note for the selected plan and preserve the **Prospective Boundary Notice** (prospective contract scenarios are NOT live bookings, vendor quotes, or granted approvals).

## Standard Execution Pipeline

Read `references/input_output_schemas.md` before writing `/app/work/parsed_plans.json`.

```bash
SCRIPTS=/skills/weekly-skill/scripts

# 1. Preflight, budget ledger, skeleton result.json, and source index
python3 "$SCRIPTS/preflight_probe.py" --input /input --output /app/output --work /app/work --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --state /app/work/.budget_state.json --init
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "challenge-7be76e91"
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# 2. Extract /app/work/parsed_plans.json faithfully (keep missing fields null) and pre-validate schema + source_ref
python3 "$SCRIPTS/validate_and_sync_result.py" validate-input \
  --input /app/work/parsed_plans.json --mode 7be76e91 --out /app/work/input_schema_audit.json

# 3. Normalize all candidates onto unified comparable schema with strict null-vs-zero & DAG math
python3 "$SCRIPTS/aligned_matrix_normalizer.py" \
  --case /app/work/parsed_plans.json \
  --out /app/work/aligned_matrix.json

# 4. Run hard-constraint pruning, Pareto dominance, 5-profile sensitivity, and selection report
python3 "$SCRIPTS/plan_choice_evaluator.py" \
  --matrix /app/work/aligned_matrix.json \
  --case /app/work/parsed_plans.json \
  --out-dir /app/output \
  --stem plan_comparison

# 5. Auto-sync /input/case.json output_contract into /app/output/result.json and run grounding + final audit
python3 "$SCRIPTS/validate_and_sync_result.py" sync-result \
  --case-json /input/case.json \
  --report /app/output/plan_comparison.json \
  --result-json /app/output/result.json \
  --task-id "challenge-7be76e91" \
  --artifact plan_comparison.json --artifact plan_comparison.md
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" --dir /app/output --result-file result.json --require plan_comparison.json --require plan_comparison.md
```

## Non-Negotiable Comparison Boundaries

- **Missing != Zero**: Never let an unspecified cost, lead time, or approval default to `0` or `True` and win the ranking.
- **No Forced Total Order on Incomplete Evidence**: Keep unresolved plans visible with exact missing fields rather than silently imputing scores.
- **Prospective != Confirmed**: Contract arithmetic evaluates planning scenarios only; live bookings, quotes, and safety/ethics approvals remain pre-execution confirmation items.

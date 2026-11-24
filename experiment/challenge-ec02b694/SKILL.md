---
name: bounded-parameter-optimization-advisor
description: >-
  Source-grounded bounded experimental parameter optimizer for 3 versioned
  candidate schedules over finite device/material domains and prospective cost
  contracts. Detects OFAT interaction reversals via OLS response-surface
  modeling, audits missing-cell estimability, checks coupled/safety limits with
  infeasibility relaxation, enforces null-vs-zero cost math, and computes
  Pareto-ranked trade-offs into /app/output.
---

# Bounded Experimental Parameter Optimization (`challenge-ec02b694`)

Generate and evaluate **3 versioned, machine-executable parameter optimization schedules (`v1`, `v2`, `v3`)** grounded in instrument manuals, material thermal/safety ceilings, pilot observations, and organizer prospective resource/price/booking contracts.

## Runtime & Budget Contract

- **Skill root**: `/skills/weekly-skill` (scripts under `/skills/weekly-skill/scripts`, references under `/skills/weekly-skill/references`).
- **Inputs**: Inspect `/input/case.json`, `/input/task.md`, equipment/material specs, finite parameter domains, historical/pilot run tables, and organizer prospective resource/price/booking contracts under `/input/`.
- **Working directory**: `/app/work` (`env.json`, `.budget_state.json`, `blocks.json`, `domain_spec.json`, `pilot_data.json`, `contract_spec.json`, `candidates_3v.json`, `domain_validation.json`, `interaction_report.json`, `cost_audit.json`, `gaps.json`).
- **Deliverables**: `/app/output/optimization_plans.json`, `/app/output/optimization_plans.md` (plus any exact filename required by `/input/case.json`), and `/app/output/result.json` preserving `case_id` and every `output_contract` field.
- **Execution budget**: 64 requests / 900 s / 16,384 output tokens per turn. Initialize `preflight_probe.py`, `budget_guard.py`, and `result_guard.py` on Turn 1.

## Five-Stage Parameter Optimization Protocol

### Stage 1: Finite Feasible Domain & Conflict Gate (`feasible_domain_validator.py`)
- Extract every parameter's `[min, max]`, `safety_max`, `step`, `allowed_values`, `unit`, and `source_ref` directly from `/input/` — never invent experience ranges.
- Encode coupled rules (`lhs_param <= rhs_param + offset`), linear budget/power bounds (`sum(coeff * param) <= rhs`), and mutually exclusive setting pairs.
- If `min > min(max, safety_max)` or the enumerated finite grid has zero feasible points, emit **`INFEASIBLE_CONSTRAINT_CONFLICT`** with the exact conflicting constraint pair and minimum required relaxation — NEVER output an out-of-range recommendation.

### Stage 2: Interaction-Reversal & Missing-Cell Audit (`interaction_and_surrogate_analyzer.py`)
- When historical or pilot run observations exist in `/input/`, run `interaction_and_surrogate_analyzer.py` BEFORE selecting candidate settings:
  - **OFAT Interaction Reversal Guard**: Fit both main-effects-only and 2-way interaction OLS response surfaces (`y ~ x_i + x_j + x_i:x_j`). Compare each factor's marginal-mean "best" setting (OFAT) against its conditional best setting at each level of interacting factors. When the optimal direction flips (`interaction_reversal_detected: true`), explicitly report the sign-flip pair and **forbid concatenating single-factor marginal optima**.
  - **Missing-Cell / Extrapolation Audit**: Map the observed factor-level grid. Flag untested combinations (e.g., high temperature tested only at low pressure, or baseline control tested at only one timepoint) as **untested / extrapolated** requiring an independent confirmatory run before claiming optimality.
  - **Prediction Uncertainty**: Compute approximate prediction standard error (`predicted_uncertainty`) from the residual variance and leverage `s * sqrt(1 + x^T (X^T X)^-1 x)`, and rank bounded feasible grid points by Upper Confidence Bound (UCB) to propose high-information next experiments.

### Stage 3: Search Strategy & Stopping Criterion Selection
Choose the search strategy explicitly based on factor count, run cost, and interaction evidence (see `references/interaction_doe_and_cost_tradeoff.md`):
- `fractional_factorial` or `screening_then_confirm`: >= 4 factors with tight run budget to screen active main effects and 2-way aliases first.
- `rsm_ccd` or `sequential_doe`: 2–4 continuous factors with suspected curvature or interaction reversal.
- `bayesian_bo`: expensive runs (> 2 h/run or high reagent cost) over bounded continuous/discrete grids where surrogate uncertainty guides sequential batches.
- `grid`: small discrete feasible domain (<= 16–25 points) affordable within budget and machine-hour caps.
- Every versioned plan MUST declare: initial design points, update/adaptation rule, quantitative `stopping_criterion` (e.g., budget exhausted, expected improvement < measurement noise `2*SD`, or convergence within tolerance), and an explicit `confirmation_run_required` gate.

### Stage 4: Strict Null-vs-Zero Cost & Throughput Accounting (`cost_resource_calculator.py`)
- Account for `one_time_setup_cost`, per-run consumables (`qty * unit_price * planned_runs`), machine booking hours (`machine_hours_per_run * planned_runs * hourly_rate`), and operator labor.
- **Strict Null-vs-Zero**: Verified `0` (`setup_cost: 0`) stays `0.0`. Missing or `null` rates, unknown usage, or omitted machine hours trigger `MISSING_RATE_OR_USAGE` (`arithmetic_ok: false`, `total_estimated_cost: null`) — NEVER coerced to `0`.

### Stage 5: Pareto Multi-Objective Ranking & Pre-mortem Gate (`candidate_plans_ranker.py`)
- Construct **3 versioned candidate plans** (`v1`–`v3`, e.g., conservative/low-cost, balanced Pareto, high-information confirmatory).
- Compute the **exact Pareto frontier** across `(predicted_response, -predicted_uncertainty, -total_cost, -total_machine_hours)` instead of optimizing a single response metric blindly.
- Separate `feasible_ranked`, `infeasible_pruned`, and `unresolved_missing_info` cleanly.
- Attach a **Pre-mortem 5-column risk table** (`failure_mode`, `early_warning_signal`, `preventive_control`, `stop_loss_trigger`, `fallback_action`) and preserve the **Prospective Boundary Notice**: surrogate predictions and cost/booking math are planning recommendations only — NOT measured future responses, vendor quotes, or EHS safety approvals.

## Standard Execution Pipeline

Read `references/input_output_schemas.md` for exact input/output JSON formats before creating `/app/work/*.json`.

```bash
SCRIPTS=/skills/weekly-skill/scripts

# 1. Preflight, budget ledger, skeleton result.json, and source index
python3 "$SCRIPTS/preflight_probe.py" --input /input --output /app/output --work /app/work --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --state /app/work/.budget_state.json --init
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "challenge-ec02b694"
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/blocks.json

# 1b. Pre-validate domain_spec.json and candidates_3v.json schema + source_ref anchors
python3 "$SCRIPTS/validate_and_sync_result.py" validate-input \
  --input /app/work/domain_spec.json --mode ec02b694 --out /app/work/domain_schema_audit.json
python3 "$SCRIPTS/validate_and_sync_result.py" validate-input \
  --input /app/work/candidates_3v.json --mode ec02b694 --out /app/work/cands_schema_audit.json

# 2. Validate finite parameter bounds, step grid, safety ceilings, coupled rules & feasible grid
python3 "$SCRIPTS/feasible_domain_validator.py" \
  --domain /app/work/domain_spec.json \
  --candidates /app/work/candidates_3v.json \
  --out /app/work/domain_validation.json

# 3. (When pilot/historical data exist) Run interaction-reversal OLS & missing-cell estimability audit
#    If no pilot data exist, omit --interaction-report in Step 5 to activate no_pilot_data_screening_mode automatically.
python3 "$SCRIPTS/interaction_and_surrogate_analyzer.py" \
  --pilot /app/work/pilot_data.json \
  --domain /app/work/domain_spec.json \
  --candidates /app/work/candidates_3v.json \
  --out /app/work/interaction_report.json

# 4. Run strict null-vs-zero prospective resource, price, and booking-hour audit
python3 "$SCRIPTS/cost_resource_calculator.py" \
  --contract /app/work/contract_spec.json \
  --candidates /app/work/candidates_3v.json \
  --out /app/work/cost_audit.json

# 5. Compute Pareto frontier, validate 3 versions, and emit JSON + Markdown deliverables
python3 "$SCRIPTS/candidate_plans_ranker.py" \
  --candidates /app/work/candidates_3v.json \
  --domain-report /app/work/domain_validation.json \
  --cost-report /app/work/cost_audit.json \
  --interaction-report /app/work/interaction_report.json \
  --out-dir /app/output \
  --stem optimization_plans

# 6. Auto-sync /input/case.json output_contract into /app/output/result.json and run grounding + final audit
python3 "$SCRIPTS/validate_and_sync_result.py" sync-result \
  --case-json /input/case.json \
  --report /app/output/optimization_plans.json \
  --result-json /app/output/result.json \
  --task-id "challenge-ec02b694" \
  --artifact optimization_plans.json --artifact optimization_plans.md
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" --dir /app/output --result-file result.json --require optimization_plans.json --require optimization_plans.md
```

## Non-Negotiable Optimization Boundaries

- **Never concatenate OFAT optima under interaction**: Always verify 2-way interaction sign flips when pilot data are present.
- **Zero out-of-range recommendations**: Reject any point breaching device range, step increment, coupled inequality, or thermal/safety ceiling.
- **Planning != Measured Outcome**: Never claim surrogate estimates as measured laboratory results or prospective contracts as confirmed quotes/approvals.

# Aligned Comparison Matrix, Null-vs-Zero Firewall, and Sensitivity Rules

## 1. Seven Unified Horizontal Comparison Dimensions

Every candidate plan must populate the exact same fields before comparison:
1. **Objective Fit & Primary Readout**: `scientific_fit_score` (`0..1`), `primary_readout`, `inference_unit`.
2. **Controls & Comparability**: `control_count`, `comparability_audit` (verifies readouts and inference units align across candidates).
3. **Normalized Full Lifecycle Cost**: `one_time_setup + consumables + machine_hours + labor + rework_reserve` (never consumables alone).
4. **Normalized Wall-Clock Schedule**: `prep_days + DAG critical path (with procurement/booking leads) + qc_turnaround_days` (never bench assay time alone).
5. **Parameter Domain & Resource Feasibility**: Finite `[min, min(max, safety_max)]` and resource availability status.
6. **Risk Level, Triggers & Fallbacks**: `low`/`medium`/`high`/`UNSPECIFIED` with explicit quantitative trigger & fallback path.
7. **Evidence Strength & Compliance**: `evidence_score` (`0..1`), `evidence_provenance_pct`, and `approval_status` (`CONFIRMED_IN_CONTRACT` / `UNVERIFIED` / `DENIED`).

## 2. Null-vs-Zero Firewall (Zero Silent Imputation)

- Explicit `0` (`one_time_setup_cost: 0`, `unit_cost: 0`, `scientific_fit_score: 0.0`) is preserved verbatim and recorded in `explicit_zero_fields`.
- Missing or `null` cost rates, setup fees, labor days, lead days, or approval statuses mark `cost_comparable: false` / `approval_status: UNVERIFIED` and route the plan into `unresolved_with_reasons` — **never coerced to `0` to outrank verified plans**.

## 3. Pareto Dominance & 5-Profile Weight Sensitivity

- First prune confirmed hard-constraint violations (`budget_cap`, `deadline_days`, `unavailable` resources, out-of-range parameters, denied approvals).
- Compute the Pareto non-dominated frontier over `(fit, evidence, -cost, -days, -risk)` and emit exact dominance pairs.
- Test rank stability across 5 distinct stakeholder weight profiles (`balanced`, `scientific_first`, `cost_first`, `speed_first`, `risk_averse`) plus exact budget/deadline slack and flip thresholds.
- When `all_infeasible: true`, output exact constraint relaxations rather than forcing an invalid winner.

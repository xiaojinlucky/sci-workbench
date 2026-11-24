---
name: experiment-feasibility-reviewer
description: >-
  Source-grounded 7-category experimental feasibility auditor for 3 versioned
  candidate protocols against organizer prospective resource, pricing, booking,
  and approval contracts. Enforces strict 4-state resource classification,
  null-vs-zero cost/approval firewalls, DAG critical-path and sample-stability
  cross-checks, internal contradiction detection, and executable verdicts into
  /app/output.
---

# Experiment Feasibility Reviewer (`challenge-2e2c5820`)

Audit **3 versioned candidate experimental plans (`v1`, `v2`, `v3`)** against actual scientific sources, equipment capability tables, SOPs, and organizer prospective resource/price/booking contracts. Produce reproducible, source-anchored, executable verdicts — never treating unwritten requirements, unverified pricing, or missing approvals as satisfied.

## Runtime & Budget Contract

- **Skill root**: `/skills/weekly-skill` (scripts under `/skills/weekly-skill/scripts`, references under `/skills/weekly-skill/references`).
- **Inputs**: Inspect `/input/case.json`, `/input/task.md`, candidate plan documents, equipment/reagent catalogs, pricing/booking schedules, and approval policies under `/input/`.
- **Working directory**: `/app/work` (`env.json`, `.budget_state.json`, `input_blocks.json`, `parsed_plans.json`, `arithmetic_report.json`, `gaps.json`).
- **Deliverables**: `/app/output/feasibility_review.json`, `/app/output/feasibility_review.md` (plus any exact filename required by `/input/case.json`), and `/app/output/result.json` preserving `case_id` and every `output_contract` field.
- **Execution budget**: 64 requests / 900 s / 16,384 output tokens per turn. Initialize `preflight_probe.py`, `budget_guard.py`, and `result_guard.py` on Turn 1.

## Seven-Category Audit Protocol & Hard Firewalls

Every candidate version MUST be audited item by item with explicit `source_ref` anchors across all **7 official categories** (details in `references/seven_category_rubric_and_states.md`):

1. **Hypotheses, Operationalized Variables & Inference Unit (`1_hypothesis_variables`)**:
   - Verify testable hypothesis, explicit `iv`/`dv`, readout method/unit, and separation of independent experimental/biological units from technical replicates. Unwritten IV/DV or unit -> `MISSING_INFO`.
2. **Control Arms & Design Estimability (`2_controls`)**:
   - Require >= 2 complementary control arms (e.g., `blank/vehicle` + `positive/standard`) covering declared confounders. Single control -> `CONDITIONAL`; zero controls -> `FAIL`. If controls are absent from one or more blocks/timepoints, flag augmented-design estimability limits.
3. **Equipment, Materials, Capability & Maintenance (`3_equipment_materials`)**:
   - Classify EVERY required item strictly into one of four states:
     - `available` (`已具备`): verified in-house, calibrated, and capable.
     - `outsourceable` (`可外协`): external service with declared turnaround & cost -> `CONDITIONAL`.
     - `needs_procurement` (`需采购`): verify `lead_days <= slack_days` and quote validity date -> `CONDITIONAL` (or `FAIL` if `lead_days` breaks deadline).
     - `unavailable` (`不具备`): hard `FAIL` unless a verified substitute with matching precision/range is bound.
   - Any missing resource record, unspecified status, expired quote, overdue calibration/maintenance, or unverified precision match -> `MISSING_INFO` or `FAIL` (never `PASS`).
4. **Schedule, Budget, Sample Pool & Stability Arithmetic (`4_time_budget_samples`)**:
   - Compute exact `total_samples = arms * biological_replicates * technical_replicates + qc_extra_samples` and verify against `sample_pool_available`.
   - Compute **DAG critical path** over `steps[].depends_on` incorporating `duration_days`, `booking_lead_days`, and procurement `lead_days` via `plan_arithmetic_verifier.py`.
   - **Strict Null-vs-Zero Firewall**: Explicit `0` (`unit_cost: 0`, `lead_days: 0`) is preserved as `verified_zero`. Missing or `null` cost rates, setup fees, or lead times stay `null` and trigger `MISSING_INFO` — NEVER coerced to `0.0`.
   - **Sample Preservation vs Lead-Time Cross-Check**: If `sample_stability_days` is shorter than `approval_lead_days + procurement_lead_days + wait_days`, flag `FAIL` (specimen degrades before execution can start).
   - **Internal Contradiction Check**: Compare plan prose claims (`declared_total_samples`, `declared_total_cost`, `declared_duration_days`) against computed arithmetic; flag `INTERNAL_ARITHMETIC_CONTRADICTION` when mismatched.
5. **Finite Parameter Domains & Safety Ceilings (`5_parameter_domains`)**:
   - Check every declared parameter against `[min, max]`, `safety_max`, `step`, and `allowed_values` anchored to `source_ref`. Out-of-range -> `FAIL`; unspecified bound or value -> `MISSING_INFO`.
6. **Risks, Stop-Loss Triggers & Fallback Paths (`6_risks_fallbacks`)**:
   - Verify quantitative failure triggers (equipment failure, reagent lot variance, sample shortfall, parameter drift), stop-loss gates, downgrade/backup routes, and human-escalation points.
7. **Ethics, Biosafety & Compliance Pathway (`7_ethics_safety`)**:
   - Audit human/animal ethics (`IRB`/`IACUC`), biosafety level (`BSL`), hazardous reagent permits, and personal-data compliance.
   - Confirmed denial (`false`) -> `FAIL`; unspecified requirement or unverified approval (`null`/omitted) -> `MISSING_INFO`. A planning contract never grants live approval.
8. **SOP Version Pinning & ELN Maintenance Cross-Check (Advisory / Handover Gate)**:
   - Flag coexisting SOP versions (`v1` vs `v2`) or missing experiment-ID/raw-data/maintenance handover ownership so the review is repeatable across staff handoffs.

## Verdict Hierarchy (Deterministic & Executable)

- `INFEASIBLE`: At least one confirmed hard blocking constraint (`FAIL`).
- `REQUIRES_INFO_OR_CONFIRMATION`: Zero confirmed `FAIL`s, but one or more `MISSING_INFO` items remain (unknown cost, unverified approval, missing resource status/spec, missing parameter value).
- `CONDITIONAL_FEASIBLE`: Zero `FAIL`s and zero `MISSING_INFO`, with concrete itemized `conditions_to_proceed` (e.g., procurement lead confirmation, outsource booking, adding a positive control).
- `FEASIBLE_UNDER_PROSPECTIVE_CONTRACT`: All 7 categories `PASS` under the organizer prospective contract (still carries the prospective boundary disclaimer).

## Standard Execution Pipeline

Read `references/input_output_schemas.md` before writing `/app/work/parsed_plans.json`.

```bash
SCRIPTS=/skills/weekly-skill/scripts

# 1. Preflight, budget ledger, skeleton result.json, and source index
python3 "$SCRIPTS/preflight_probe.py" --input /input --output /app/output --work /app/work --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --state /app/work/.budget_state.json --init
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "challenge-2e2c5820"
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# 2. Auto-extract source_ref-anchored draft from raw /input narrative files (missing fields stay null / NOT_FOUND_IN_SOURCE), then validate schema
python3 "$SCRIPTS/source_to_plan_extractor.py" \
  --input-dir /input --blocks /app/work/input_blocks.json --mode 2e2c5820 --out /app/work/parsed_plans.json
python3 "$SCRIPTS/validate_and_sync_result.py" validate-input \
  --input /app/work/parsed_plans.json --mode 2e2c5820 --out /app/work/input_schema_audit.json

# 3. Run deterministic DAG schedule, null-vs-zero cost, stability, capability, and contradiction arithmetic
python3 "$SCRIPTS/plan_arithmetic_verifier.py" \
  --case /app/work/parsed_plans.json \
  --out /app/work/arithmetic_report.json

# 4. Run 7-category + handover feasibility audit across all candidate versions
python3 "$SCRIPTS/feasibility_contract_auditor.py" \
  --case /app/work/parsed_plans.json \
  --arith /app/work/arithmetic_report.json \
  --out-dir /app/output \
  --stem feasibility_review

# 5. Auto-sync /input/case.json output_contract into /app/output/result.json and run grounding + final audit
python3 "$SCRIPTS/validate_and_sync_result.py" sync-result \
  --case-json /input/case.json \
  --report /app/output/feasibility_review.json \
  --result-json /app/output/result.json \
  --task-id "challenge-2e2c5820" \
  --artifact feasibility_review.json --artifact feasibility_review.md
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" --dir /app/output --result-file result.json --require feasibility_review.json --require feasibility_review.md
```

## Non-Negotiable Boundaries

- **Unwritten != Satisfied**: Never infer missing approvals, missing unit prices, or unlisted equipment as available or zero-cost.
- **Prospective != Live Execution**: Preserve the explicit prospective boundary notice — scenarios under organizer contracts are NOT confirmed lab bookings, vendor quotes, or granted ethics/safety approvals.
- **Traceable Locators**: Cite exact file, section, table row, or contract key in `source_ref` for every `PASS`, `CONDITIONAL`, `MISSING_INFO`, and `FAIL` judgment.

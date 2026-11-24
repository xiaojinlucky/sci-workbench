# Input & Output JSON Schemas (`challenge-cf1995b5`)

## 1. Input Schema: `/app/work/parsed_goal.json`

Construct `/app/work/parsed_goal.json` from `/input/case.json`, `/input/task.md`, and source documents:

```json
{
  "case_id": "case-01",
  "title": "Concise study title",
  "research_type": "empirical_causal | engineering_optimization | exploratory_discovery | computational_benchmark",
  "hypothesis": {
    "statement": "Testable falsifiable hypothesis statement",
    "iv": "Independent variable with levels and units",
    "dv": "Primary dependent variable",
    "dv_unit": "e.g., %, MPa, uM, fold-change",
    "measurement_method": "Exact assay or instrument readout",
    "falsification_criterion": "Quantitative threshold that refutes the hypothesis",
    "confounders": ["solvent_carrier", "batch_drift"]
  },
  "statistical_design": {
    "inference_unit": "Independent biological replicate / reactor batch",
    "technical_unit": "Triplicate assay well / injection",
    "planned_biological_replicates": 4,
    "randomization_strategy": "Stratified block randomization across instrument slots",
    "blocking_factors": ["batch_drift"],
    "expected_cohen_d": 1.6,
    "alpha": 0.05,
    "target_power": 0.8
  },
  "controls": [
    {"name": "vehicle_blank", "type": "vehicle", "addresses_confounder": "solvent_carrier", "source_ref": "Methods p.4"},
    {"name": "reference_positive", "type": "positive", "addresses_confounder": "assay_sensitivity", "source_ref": "SOP-02@v2.0"}
  ],
  "design_cells": [
    {"arm": "vehicle_blank", "block_or_timepoint": "24h", "is_control": true},
    {"arm": "treat_low", "block_or_timepoint": "24h", "is_control": false}
  ],
  "sop_references": [
    {"sop_id": "SOP-02", "sop_version": "v2.0", "source_ref": "input/sop_manifest.md"}
  ],
  "eln_handover": {
    "experiment_id_pattern": "EXP-{YYYYMMDD}-{ARM}-{REP}",
    "raw_data_binding": "raw_data/{experiment_id}/",
    "deviation_log_fields": ["planned_setting", "actual_setting", "timestamp_utc", "validity_impact"]
  },
  "objectives": [
    {"name": "target_conversion_pct", "direction": "maximize", "weight": 0.65, "scale_min": 0, "scale_max": 100, "min_acceptable": 75.0},
    {"name": "byproduct_pct", "direction": "minimize", "weight": 0.35, "scale_min": 0, "scale_max": 25, "max_acceptable": 8.0}
  ],
  "prospective_contract": {
    "budget_cap_usd": 4500.0,
    "deadline_days": 14.0,
    "max_samples": 48,
    "setup_cost_usd": 350.0,
    "per_sample_cost_usd": 45.0,
    "labor_rate_per_day": 200.0,
    "rework_reserve_rate": 0.1,
    "base_prep_days": 1.0,
    "qc_days": 1.5,
    "biosafety_approved": null,
    "ethics_approval_required": false,
    "equipment_booking_confirmed": null,
    "vendor_quote_confirmed": null,
    "resources": {
      "reactor_A": {"status": "available", "rate_per_hr": 35.0, "booking_lead_days": 2.0, "lead_days": 0.0, "substitutable": false, "source_ref": "contract.json:reactor_A"},
      "gc_ms": {"status": "available", "rate_per_hr": 50.0, "booking_lead_days": 1.0, "lead_days": 0.0, "substitutable": true, "source_ref": "contract.json:gc_ms"}
    },
    "parameter_domains": {
      "temperature_c": {"min": 180.0, "max": 260.0, "safety_max": 250.0, "step": 10.0, "unit": "C", "source_ref": "manual.pdf p.12"}
    },
    "coupled_constraints": [
      {"lhs_param": "temperature_c", "op": "<=", "rhs_param": "pressure_bar", "offset": 230.0}
    ]
  },
  "finite_candidates": [
    {
      "version": "v1_baseline",
      "rationale": "Initial literature transfer before budget/safety pruning",
      "treatment_arms": 4,
      "biological_replicates": 4,
      "technical_replicates": 2,
      "qc_extra_samples": 4,
      "labor_days": 4.0,
      "parameters": {"temperature_c": 260.0, "pressure_bar": 20.0},
      "predicted_metrics": {"target_conversion_pct": 88.0, "byproduct_pct": 12.0},
      "steps": [
        {"id": "prep", "duration_days": 1.0, "resource": "reactor_A", "hours": 4.0, "sop_id": "SOP-02", "sop_version": "v2.0", "depends_on": []},
        {"id": "assay", "duration_days": 2.0, "resource": "gc_ms", "hours": 6.0, "sop_id": "SOP-02", "sop_version": "v2.0", "depends_on": ["prep"]}
      ],
      "stop_loss": "Halt if blank drift > 5% or catalyst mass balance < 92%",
      "fallback_path": "Switch GC-MS to backup HPLC-RID (substitutable) and re-run QC standard"
    }
  ]
}
```

## 2. Output Contract: `/app/output/result.json`

Preserve `case_id` and every field defined in `/input/case.json`'s `output_contract`. Include:
- `artifacts`: `["optimized_plan.json", "optimized_plan.md"]`
- `experimental_plan`: link/summary of the recommended plan version, 6 elements, and 3-version diff
- `rationale_and_risks`: `source_ref` table, assumptions, stop-loss rules, and `prospective_boundary_notice`
- `confirmation_items`: structured list of unverified/required approvals and bookings
- `gaps`: explicit unresolved conflicts, missing contract rates, or SOP version clashes

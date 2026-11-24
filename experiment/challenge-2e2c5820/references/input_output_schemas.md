# Input & Output JSON Schemas (`challenge-2e2c5820`)

## 1. Input Schema: `/app/work/parsed_plans.json`

Leave unwritten fields as `null` (never coerce missing costs, lead days, or approvals to `0` or `true`):

```json
{
  "case_id": "case-01",
  "title": "Study Feasibility Audit",
  "prospective_contract": {
    "evaluation_date": "2026-11-24",
    "budget_cap_usd": 5000.0,
    "deadline_days": 20.0,
    "sample_pool_available": 48,
    "sample_stability_days": 14.0,
    "approval_lead_days": 5.0,
    "labor_rate_per_day": 200.0,
    "biosafety_approved": true,
    "ethics_approval_required": true,
    "ethics_approved": null,
    "hazardous_reagent_permit_required": false,
    "compliance_source_ref": "contract.json:compliance",
    "resources": {
      "lc_ms": {
        "status": "available",
        "rate_per_hr": 65.0,
        "booking_lead_days": 2.0,
        "lead_days": 0.0,
        "spec_precision": 0.01,
        "maintenance_status": "current",
        "source_ref": "equipment_table.md#L12"
      },
      "custom_reagent": {
        "status": "needs_procurement",
        "unit_cost": 120.0,
        "lead_days": 7.0,
        "quote_valid_until": "2026-12-15",
        "source_ref": "vendor_sheet.md#L5"
      }
    },
    "parameter_domains": {
      "column_temp_c": {"min": 25.0, "max": 60.0, "safety_max": 55.0, "step": 5.0, "unit": "C", "source_ref": "manual.pdf p.9"}
    }
  },
  "candidate_plans": [
    {
      "version": "v1_initial",
      "source_ref": "plans.md#v1",
      "hypothesis": "Testable hypothesis",
      "variables": {"iv": "Dose level", "dv": "AUC_0-24h", "dv_unit": "ng*h/mL"},
      "inference_unit": "Independent biological donor",
      "controls": ["vehicle_blank", "reference_standard"],
      "arms": 4,
      "biological_replicates": 4,
      "technical_replicates": 2,
      "qc_extra_samples": 4,
      "declared_total_samples": 36,
      "one_time_setup_cost": 250.0,
      "labor_days": 3.0,
      "prep_days": 1.0,
      "qc_days": 1.0,
      "parameters": {"column_temp_c": 40.0},
      "steps": [
        {"id": "procure", "duration_days": 1.0, "resource": "custom_reagent", "units": 4, "sop_id": "SOP-LC-02", "sop_version": "v2.0", "depends_on": []},
        {"id": "run_lcms", "duration_days": 2.0, "resource": "lc_ms", "hours": 10.0, "required_precision": 0.02, "sop_id": "SOP-LC-02", "sop_version": "v2.0", "depends_on": ["procure"]}
      ],
      "risks": [
        {"name": "drift", "trigger": "QC standard RSD > 8%", "mitigation": "Flush column and re-run 5-point curve"}
      ]
    }
  ]
}
```

## 2. Output Contract: `/app/output/result.json`

Preserve `case_id` and every field from `/input/case.json`'s `output_contract`, attaching `feasibility_review.json` & `feasibility_review.md` under `artifacts`, itemized per-version verdicts, `conditions_to_proceed`, `missing_information`, `hard_failures`, and `prospective_boundary_notice`.

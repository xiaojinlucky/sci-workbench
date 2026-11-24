# Input & Output JSON Schemas (`challenge-7be76e91`)

## 1. Input Schema: `/app/work/parsed_plans.json`

Preserve explicit `0` as `0.0` and unknown/omitted fields as `null`:

```json
{
  "case_id": "case-01",
  "title": "Aligned 3-Version Experimental Plan Comparison",
  "prospective_contract": {
    "budget_cap_usd": 4000.0,
    "deadline_days": 15.0,
    "labor_rate_per_day": 180.0,
    "rework_reserve_rate": 0.1,
    "biosafety_approved": true,
    "ethics_approval_required": false,
    "resources": {
      "cryo_sem": {"status": "available", "rate_per_hr": 80.0, "booking_lead_days": 2.0, "lead_days": 0.0},
      "reagent_a": {"status": "needs_procurement", "unit_cost": 120.0, "lead_days": 5.0}
    },
    "parameter_domains": {
      "voltage_kv": {"min": 1.0, "max": 15.0, "safety_max": 10.0}
    }
  },
  "candidate_plans": [
    {
      "version": "v1_plan",
      "source_ref": "plans.md#v1",
      "primary_readout": "Pore diameter distribution (nm)",
      "inference_unit": "Independent scaffold batch",
      "controls": ["blank_substrate", "reference_mesh"],
      "one_time_setup_cost": 0.0,
      "labor_days": 3.0,
      "prep_days": 1.0,
      "qc_turnaround_days": 1.0,
      "parameters": {"voltage_kv": 5.0},
      "steps": [
        {"id": "prep", "duration_days": 1.0, "resource": "reagent_a", "units": 3, "depends_on": []},
        {"id": "img", "duration_days": 2.0, "resource": "cryo_sem", "hours": 6.0, "depends_on": ["prep"]}
      ],
      "risk_level": "low",
      "risk_trigger": "Beam damage drift > 5%",
      "fallback_plan": "Reduce accelerating voltage to 3 kV",
      "scientific_fit_score": 0.88,
      "evidence_score": 0.85
    }
  ]
}
```

## 2. Output Contract: `/app/output/result.json`

Preserve `case_id` and every field from `/input/case.json`'s `output_contract`, listing `["plan_comparison.json", "plan_comparison.md"]` in `artifacts`, selected version (or `null` when infeasible/unresolved), drop rationales, Pareto/sensitivity summary, and `prospective_boundary_notice`.

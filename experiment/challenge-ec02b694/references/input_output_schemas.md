# Input & Output JSON Schemas (`challenge-ec02b694`)

## 1. `/app/work/domain_spec.json`
```json
{
  "parameters": {
    "temp_c": {"min": 180.0, "max": 260.0, "safety_max": 240.0, "step": 10.0, "unit": "C", "source_ref": "manual#p4"},
    "flow_sccm": {"min": 20.0, "max": 80.0, "step": 10.0, "unit": "sccm", "source_ref": "manual#p6"}
  },
  "coupled_constraints": [
    {"lhs_param": "temp_c", "op": "<=", "rhs_param": "flow_sccm", "offset": 200.0}
  ],
  "linear_constraints": [
    {"name": "thermal_load_cap", "terms": {"temp_c": 1.0, "flow_sccm": 0.5}, "op": "<=", "rhs": 270.0}
  ],
  "mutual_exclusions": []
}
```

## 2. `/app/work/pilot_data.json` (Optional when pilot/historical data exist)
```json
{
  "response_metric": "yield_pct",
  "objective_direction": "maximize",
  "factors": ["temp_c", "flow_sccm"],
  "runs": [
    {"run_id": "P1", "temp_c": 190, "flow_sccm": 20, "yield_pct": 82.0},
    {"run_id": "P2", "temp_c": 230, "flow_sccm": 20, "yield_pct": 54.0},
    {"run_id": "P3", "temp_c": 190, "flow_sccm": 60, "yield_pct": 58.0},
    {"run_id": "P4", "temp_c": 230, "flow_sccm": 60, "yield_pct": 91.0}
  ]
}
```

## 3. `/app/work/contract_spec.json` & `/app/work/candidates_3v.json`
- `contract_spec.json`: `currency`, `budget_cap`, `time_limit_hours`, `machine_hour_rate`, `setup_cost`, `unit_prices` (`null` stays unverified, explicit `0` stays verified zero).
- `candidates_3v.json`: `{"plans": [{"plan_id": "v1_...", "parameters": {...}, "planned_runs": 4, "machine_hours": 2.0, "resource_usage": {...}, "search_strategy": "sequential_doe", "stopping_criterion": "...", "predicted_response": 89.5, "predicted_uncertainty": 2.1, "confirmation_run_required": true}]}`.

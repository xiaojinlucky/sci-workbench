# Minimal Example Walkthrough (Illustrative Only)

> Note: Illustrative interface example only, not an actual case result or reported score.

## Example Invocation
```bash
python3 scripts/trend_evolution_analyzer.py --input /input --output /app/output --work /app/work
python3 scripts/trend_evolution_analyzer.py --input /input --verify-report /app/output/trend_evolution_report.md --out-json /app/work/trend_verify.json --strict
```

## Example Output Snippet (`/app/output/result.json`, Illustrative)
```json
{
  "case_id": "example-trend-01",
  "records_total": 42,
  "date_basis_breakdown": {"published": 31, "online_first": 4, "preprint": 6, "unknown": 1},
  "largest_step_year": 2024,
  "stage_representatives": [
    {"phase": "foundational_origin", "year": 2019},
    {"phase": "methodological_pioneer_long_tail", "year": 2022},
    {"phase": "phase_transition_bridge", "year": 2023},
    {"phase": "recent_frontier_anchor", "year": 2025}
  ]
}
```

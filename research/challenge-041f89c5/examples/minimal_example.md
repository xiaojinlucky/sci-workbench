# Minimal Example Walkthrough (Illustrative Only)

> Note: Illustrative interface example only; values do not represent an actual run or benchmark output.

## Example Invocation
```bash
python3 scripts/novelty_feasibility_evaluator.py --input /input --output /app/output --work /app/work
python3 scripts/novelty_feasibility_evaluator.py --input /input --eval-json /app/work/topic_eval_spec.json --output /app/output --work /app/work --strict
```

## Example Output Snippet (`/app/output/result.json`, Illustrative)
```json
{
  "case_id": "example-topic-01",
  "go_no_go_decision": "FEASIBLE_WITH_CONDITIONS",
  "novelty_checks": [
    {"claim_id": "NOV-1", "novelty_type": "scenario_transfer", "verdict": "PARTIALLY_COVERED_INCREMENTAL"}
  ],
  "feasibility_checks": [
    {"axis": "data_availability", "tag": "GROUNDED", "source_ref": "case.json", "source_ref_verified": true},
    {"axis": "compute_budget", "tag": "BLOCKED_UNKNOWN", "source_ref": null}
  ]
}
```

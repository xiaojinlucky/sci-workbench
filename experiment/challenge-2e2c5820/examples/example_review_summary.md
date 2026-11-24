# Illustrative Call & Expected Output (`challenge-2e2c5820`)

> **Notice**: Illustrative example only — NOT a formal competition case or live lab approval.

## Minimal Invocation

```bash
SCRIPTS=/skills/weekly-skill/scripts
python3 "$SCRIPTS/plan_arithmetic_verifier.py" \
  --case /skills/weekly-skill/examples/minimal_feasibility_case.json \
  --out /app/work/arithmetic_report.json

python3 "$SCRIPTS/feasibility_contract_auditor.py" \
  --case /skills/weekly-skill/examples/minimal_feasibility_case.json \
  --arith /app/work/arithmetic_report.json \
  --out-dir /app/output \
  --stem feasibility_review
```

## Illustrative Output Summary

- `v1_unpriced`: `REQUIRES_INFO_OR_CONFIRMATION` — `unpriced_kit.unit_cost` is `null` and quote date is missing; total cost remains `null` (never coerced to `$0`).
- `v2_overtemp`: `INFEASIBLE` — `column_temp_c=55.0` exceeds `safety_max=50.0` (`lc_manual#p14`).
- `v3_conditional_ok`: `CONDITIONAL_FEASIBLE` — exact total cost `$1610.0` (<= `$4200`), DAG critical path `10.0d` (<= `18d`), pre-run wait `10.0d` <= `sample_stability_days` (`14.0d`), with 1 itemized condition: confirm `isotope_std` 6-day procurement lead (`quotes.md#iso`).

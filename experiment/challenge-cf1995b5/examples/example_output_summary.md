# Illustrative Call & Expected Output (`challenge-cf1995b5`)

> **Notice**: This is a minimal illustrative example demonstrating interface usage only. It is NOT a formal competition case or executed laboratory result.

## Minimal Invocation

```bash
SCRIPTS=/skills/weekly-skill/scripts
python3 "$SCRIPTS/hypothesis_control_mapper.py" \
  --case /skills/weekly-skill/examples/minimal_case_example.json \
  --out /app/work/control_audit.json

python3 "$SCRIPTS/plan_domain_optimizer.py" \
  --case /skills/weekly-skill/examples/minimal_case_example.json \
  --controls /app/work/control_audit.json \
  --out-dir /app/output \
  --stem optimized_plan

python3 "$SCRIPTS/plan_report_verifier.py" \
  --plan /app/output/optimized_plan.json \
  --case /skills/weekly-skill/examples/minimal_case_example.json \
  --out /app/work/report_audit.json
```

## Illustrative Output Summary

- **Control & Hypothesis Audit**: `ok: true`, `control_count: 2` (`blank` + `positive`), `unmatched_confounders: []`, `power_audit.achieved_power_approx: 0.7224` (transparently flags slight underpower relative to 0.80 target if `n=4` at `d=1.8`, recommending `n=5` if budget allows), `estimability_audit.structure: "complete_factorial"`, `sop_pin_audit.ok: true`.
- **3-Version Evaluation**:
  - `v1_high_temp`: **INFEASIBLE** (`temperature_c=250.0` violates `safety_max=245.0`; `co_byproduct_pct=10.5 > max_acceptable 8.0`).
  - `v2_compact_grid`: **FEASIBLE** (total samples `22`, DAG critical path `6.0d`, strict cost within `$3800`).
  - `v3_staged_optimal`: **RECOMMENDED** (total samples `24`, DAG critical path `6.5d`, higher selectivity `87.4%` & lower byproduct `4.8%`, all safety & budget bounds satisfied).
- **Confirmation Items**: `biosafety_approved`, `equipment_booking_confirmed`, and `vendor_quote_confirmed` remain explicitly `UNVERIFIED_REQUIRED` under the prospective planning disclaimer.

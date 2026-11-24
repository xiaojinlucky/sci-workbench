# Minimal Synthetic Example (Illustrative Only — Not a Formal Case Result)

## Input
- `examples/synthetic_cohort.csv`: 13 rows containing leading-zero IDs (`0001..0012`), a coded integer column (`group_code`), 1 exact duplicate (`0004`), 1 explicit missing value (`0008: NA`), and signed negative values in `Inhibitor`.

## Invocation
```bash
# Run full 5-stage pipeline
python3 scripts/multistep_pipeline_engine.py \
  --input examples/synthetic_cohort.csv \
  --group-col group \
  --value-col metric_val \
  --unit "a.u." \
  --case-id synthetic-demo \
  --out-dir /tmp/demo_out \
  --stem demo_analysis

# Demonstrate interruption & verified checkpoint resume
python3 scripts/multistep_pipeline_engine.py --input examples/synthetic_cohort.csv --group-col group --value-col metric_val --out-dir /tmp/demo_resume --stem demo_analysis --fail-at-stage S3_stats
python3 scripts/multistep_pipeline_engine.py --input examples/synthetic_cohort.csv --group-col group --value-col metric_val --out-dir /tmp/demo_resume --stem demo_analysis --resume
```

## Schematic Output (Illustrative)
- On interruption at `S3_stats`, `demo_analysis_checkpoint.json` records `S1_intake=COMPLETED`, `S2_clean=COMPLETED`, `S3_stats=FAILED`, `S4_viz=PENDING`, `S5_report=PENDING` and exits non-zero.
- On `--resume`, `S1_intake` and `S2_clean` are verified via disk SHA-256 and skipped (`resumed_stages=["S1_intake", "S2_clean"]`), while `S3_stats`, `S4_viz`, and `S5_report` complete cleanly with reconciled row count (`11` analyzed rows after dropping 1 exact duplicate and 1 `NA`).

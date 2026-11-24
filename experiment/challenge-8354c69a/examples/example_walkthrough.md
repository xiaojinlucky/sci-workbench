# Illustrative Call & Expected Output (`challenge-8354c69a`)

> **Notice**: Illustrative example only — NOT a formal competition case or measured run.

```bash
SCRIPTS=/skills/weekly-skill/scripts
python3 "$SCRIPTS/explanation_5part_auditor.py" \
  --report /skills/weekly-skill/examples/example_5part_interpretation.json \
  --out /app/work/5part_audit.json \
  --md-out /app/output/interpretation_report.md
```

- Verifies all 5 required sections, 2 distinct causal attribution layers (`batch_channel_or_plate_effect` + `operator_or_protocol_deviation`), concrete executable checklist items, honest `partial` status when `data_gap_count=2` (`1 missing`, `1 below_lod`, kept distinct from `explicit_zero_count=1`), and retained unit/LOQ discrepancy.

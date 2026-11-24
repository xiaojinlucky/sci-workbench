# Minimal Synthetic Invocation Example (Illustration Only)

> Note: Synthetic interface example only; not a formal evaluation case.

## Command
```bash
python3 scripts/rd_row_provenance_auditor.py index --data-dir /input --out-json /app/work/rd_data_index.json
python3 scripts/rd_row_provenance_auditor.py audit --data-dir /input --draft /app/output/results_and_discussion.md --out-json /app/work/rd_audit_report.json
python3 scripts/rd_row_provenance_auditor.py seal --data-dir /input --draft /app/output/results_and_discussion.md --case-id EXAMPLE-RD-01 --out-dir /app/output
```

## Illustrative Audit Finding (Adversarial Row-Condition Mismatch)
```json
{
  "code": "ROW_CONDITION_VALUE_MISMATCH",
  "severity": "high",
  "section": "Results",
  "named_condition": "Low-dose",
  "misattributed_value": "38.6",
  "actual_owner_conditions": ["High-dose"]
}
```

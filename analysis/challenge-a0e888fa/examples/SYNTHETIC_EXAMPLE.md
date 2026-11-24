# Minimal Synthetic Example (Illustrative Only — Not a Formal Case Result)

## Input
- `examples/synthetic_dirty_table.csv`: 7 synthetic rows demonstrating leading-zero ID distinction (`00123` vs `123`), true zero (`0`), sentinel (`-999`), `<LOD`, unit conversion (`0.02 g/L -> 20 mg/L`), and a conflicting primary-key duplicate (`00127`: `24.0` vs `29.5`).
- `examples/synthetic_workorder.json`: Per-metric cleaning contract with `forbid_imputation: true`.

## Invocation
```bash
python3 scripts/dirty_table_fault_scanner.py --input examples/synthetic_dirty_table.csv --workorder examples/synthetic_workorder.json --out /tmp/scan.json
python3 scripts/reversible_table_cleaner.py --input examples/synthetic_dirty_table.csv --workorder examples/synthetic_workorder.json --out-dir /tmp/out --stem demo
python3 scripts/quality_reconciler.py --csv /tmp/out/demo.csv --manifest /tmp/out/demo_manifest.json --quarantine /tmp/out/demo_quarantine_conflicts.csv --out-md /tmp/out/demo_quality.md --out-json /tmp/out/demo_reconciliation.json
```

## Schematic Output (Illustrative)
- `demo.csv`: 5 cleaned rows (`00123` retained as `"00123"` with value `"0"` and `true_zero_preserved`; `123` retained as distinct `"123"` with `"12.4"`; `00124` blank with `instrument_sentinel`; `00125` blank with `below_lod`; `00126` converted to `"20"` `mg/L` with `reported_value_raw="0.02 g/L"`).
- `demo_quarantine_conflicts.csv`: 2 quarantined rows for key `("00127", "CRP")` requiring expert review.
- `demo_reconciliation.json`: `{"ok": true, "reconciliation_passed": true, "rows_raw": 7, "rows_cleaned_disk": 5, "rows_quarantined_conflicts_disk": 2, "mismatches": []}`.

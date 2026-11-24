# Workorder Schema, Per-Metric Units & Published Aggregate Rules

## 1. Hard Rules for Published Aggregate Transcriptions

When the input represents transcribed summary tables from published literature (rather than raw individual-level measurements):
1. **Zero Imputation**: Do not fill missing, `<LOD`, `ND`, `-999`, or invalid cells with column means, medians, zeros, or regression predictions. Set `forbid_imputation: true`.
2. **Zero Population Inference**: Do not back-calculate individual-level variance, confidence intervals, or sample distributions that are not explicitly recorded in the source table. Set `forbid_population_inference: true`.
3. **No Presumed Gold File**: Never hardcode or expect a runtime `quality-gold.md` file. All rules come from the case's data dictionary and workorder.

## 2. Workorder JSON Contract (Long-Table & Wide-Table Modes)

### Long-Table Mode (Per-Metric Rows)
Specify `metric_column`, `value_column`, `unit_column`, and `per_metric_rules`:
```json
{
  "schema_version": "2.0",
  "table_kind": "published_aggregate_transcription",
  "forbid_imputation": true,
  "forbid_population_inference": true,
  "primary_key": ["record_id", "metric_name"],
  "id_columns_preserve_string": ["record_id"],
  "preserve_leading_zeros": true,
  "duplicate_policy": {
    "exact_duplicate": "drop_keep_first_with_log",
    "key_conflict": "quarantine_do_not_silently_overwrite"
  },
  "metric_column": "metric_name",
  "value_column": "reported_value",
  "unit_column": "unit",
  "per_metric_rules": {
    "CRP": {
      "target_unit": "mg/L",
      "unit_factors_to_target": {"mg/L": 1.0, "ug/mL": 1.0, "g/L": 1000.0},
      "qc_bounds": {"min": 0.0, "max": 500.0}
    }
  }
}
```

### Wide-Table Mode (Per-Column Metrics)
Omit `metric_column` and specify `numeric_cols`: `["crp_val", "albumin_val"]`. Keys inside `per_metric_rules` then match the exact column names.

## 3. Reversible Unit Conversion & Scale Conflict Handling

1. **Inline unit vs column unit verification**:
   - If a cell carries an inline unit (e.g., `"12.4 ug/mL"`) and the `unit` column carries `"mg/L"`, compare their conversion factors to `target_unit`.
   - If equivalent (`ug/mL` and `mg/L` both have factor `1.0` to `mg/L`), proceed cleanly and log the inline extraction.
   - If their scales disagree (e.g., inline `"g/L"` vs column `"mg/L"` where factors are `1000.0` vs `1.0`), **do not guess which is right**: emit `value_state = "unit_conflict_unresolved"`, leave the cleaned value blank, keep both `*_raw` and `*_unit_raw`, and log a gap.
2. **Unknown unit**: If an observed unit is absent from `unit_factors_to_target` and differs from `target_unit`, emit `value_state = "unknown_unit_unconverted"` rather than assuming factor `1.0`.
3. **Physical QC bounds**: Out-of-bounds converted values (e.g., negative concentration `−4.2 pg/mL` or extreme `1240.5 mg/L > 500`) are retained numerically for full auditability and tagged `value_state = "qc_out_of_bounds"` with an entry in `qc_alerts`.

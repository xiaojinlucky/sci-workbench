# Illustrative SQLite Workflow Trace (Example Only)

> Note: All commands and summaries below are **illustrative examples** showing the Skill's CLI contract and verification artifacts; they do NOT represent precomputed formal benchmark answers.

## 1. Strictly Read-Only Schema, Integrity & Cardinality Inspection

```bash
python3 -B /skills/weekly-skill/scripts/sqlite_schema_inspector.py \
  --db /input/scientific_snapshot.sqlite \
  --out /app/work/db_schema_audit.json \
  --md-out /app/work/db_schema_audit.md
```

What to read from `db_schema_audit.json` before authoring SQL:
- `db_immutability_verified: true` (`db_sha256_before == db_sha256_after`).
- `coded_integer_summary`: identifies categorical codes (`cultivar_code`, `species_code`) and binary flags (`qc_pass`) so you never compute meaningless `AVG(cultivar_code)`.
- `leading_zero_text_summary`: identifies TEXT primary/foreign keys (`sample_code='0001'`, `specimen_id='001'`) that must stay text.
- `relationship_cardinalities`: identifies `1:N` child edges (`join_inflation_risk: true`) requiring CTE pre-aggregation and parent keys with `zero_child_parent_count > 0` requiring `LEFT JOIN`.

## 2. Read-Only Cross-Table Execution, Guards & Independent Python Reconciliation

```bash
python3 -B /skills/weekly-skill/scripts/sql_cross_table_runner.py \
  --db /input/scientific_snapshot.sqlite \
  --query-spec /skills/weekly-skill/examples/example_query_spec.json \
  --out-dir /app/output \
  --stem sql_analysis
```

Expected artifacts under `/app/output/`:
- `sql_analysis_report.json` — includes DB SHA-256 immutability proof, `EXPLAIN QUERY PLAN`, JOIN inflation checks, `empty_set_classification` (`LEGITIMATE_EMPTY_GROUP` vs `ANOMALOUS_EMPTY_RESULT`), and `python_reconciliation` (`max_abs_diff < 1e-9`).
- `sql_analysis_report.md` — concise human-readable query audit report.
- `sql_analysis_cultivar_summary_safe_cte.csv` — verified CSV export preserving NULL values for empty groups.
- `sql_analysis_chart.svg` — editable pure-stdlib vector chart (plus `.png`/`.pdf` if Matplotlib is installed in the container).

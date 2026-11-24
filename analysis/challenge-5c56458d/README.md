# Scientific SQLite Query & Cross-Table Analysis Skill (`challenge-5c56458d`)

A self-contained, offline-capable Harbor Skill for read-only scientific SQLite schema discovery, cross-table JOIN cardinality control, empty-group triage, independent Python aggregate reconciliation, and verifiable visual reporting.

## Key Capabilities

- **Zero-Dependency Native Core**: Runs 100% natively on Python 3.12 standard library (`sqlite3`, `csv`, `json`, `math`, `statistics`, `difflib`) inside minimal offline containers (`python:3.12-slim`) with zero reliance on `pandas` or `matplotlib`. Automatically adds Matplotlib 300-dpi PNG/PDF previews only when Matplotlib is genuinely installed.
- **Read-Only Schema, Integrity & Cardinality Inspector (`scripts/sqlite_schema_inspector.py`)**: Opens SQLite strictly via `file:<db>?mode=ro` + `PRAGMA query_only = ON`, verifies identical pre/post DB SHA-256 hashes, audits PK uniqueness, exact duplicate rows, FK orphan records, zero-child parent keys, coded-integer columns (`cultivar_code`, `species_code`, `qc_pass`), leading-zero string IDs (`'0001'`), constraint-loss warnings, and automatic `1:1` vs `1:N` inter-table cardinality inference.
- **Cross-Table SQL Runner & Reconciliation Engine (`scripts/sql_cross_table_runner.py`)**: Enforces a read-only SQL fence, captures `EXPLAIN QUERY PLAN`, performs schema-aware SQL error self-localization, blocks 1-to-N JOIN row inflation (both detail-level and aggregated queries) with concrete pre-aggregation CTE rewrite hints, classifies `LEGITIMATE_EMPTY_GROUP` vs `ANOMALOUS_EMPTY_RESULT`, detects coded-column mis-aggregation (`AVG(cultivar_code)`), reconciles SQL aggregates against independent native Python recalculation (`max_abs_diff < 1e-9`), and renders editable stdlib SVG charts.
- **Harbor Core Suite (`M1-M6`)**: Includes `preflight_probe.py`, `result_guard.py`, `budget_guard.py`, `block_indexer.py`, `gap_gate.py`, and `final_audit.py`.

## Package Layout

```text
.
├── SKILL.md
├── README.md
├── references/
│   ├── readonly-schema-and-coded-columns.md
│   ├── join-inflation-guard-and-cte-patterns.md
│   └── empty-group-triage-and-reconciliation.md
├── scripts/
│   ├── preflight_probe.py
│   ├── result_guard.py
│   ├── budget_guard.py
│   ├── block_indexer.py
│   ├── gap_gate.py
│   ├── final_audit.py
│   ├── sqlite_schema_inspector.py
│   └── sql_cross_table_runner.py
└── examples/
    ├── example_query_spec.json
    ├── example_queries.sql
    └── example_workflow_trace.md
```

## Quick Verification

```bash
python3 -B scripts/sqlite_schema_inspector.py --db /path/to/snapshot.sqlite --out /tmp/schema.json --md-out /tmp/schema.md
python3 -B scripts/sql_cross_table_runner.py --db /path/to/snapshot.sqlite --query-spec examples/example_query_spec.json --out-dir /tmp/sql_out --stem demo_sql
```

## Runtime & Compliance Notes

- Targets the **Scientific Python + SQLite** sandbox exclusively (no Office, PDF/LaTeX, OCR, or network dependency).
- All packaged resources are UTF-8 text files without symlinks, binary blobs, `.tsv` files, credentials, or host-specific paths.

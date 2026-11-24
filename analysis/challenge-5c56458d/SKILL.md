---
name: scientific-sqlite-query-analyzer
description: >-
  Inspect scientific SQLite databases strictly read-only, ground schemas and
  coded-integer/leading-zero ID semantics against data dictionaries, plan safe
  join keys and 1:N cardinality pre-aggregation CTEs, execute SQL with EXPLAIN
  QUERY PLAN and automated JOIN inflation guards, distinguish legitimate empty
  dimension groups from anomalous empty queries, cross-verify SQL aggregates
  via independent native Python recalculation (max_abs_diff < 1e-9), and emit
  pure-stdlib SVG visual reports under /app/output.
---

# Scientific SQLite Query & Cross-Table Analysis Workflow

## 1. Mission, Runtime Boundaries & Scope

This skill governs end-to-end **read-only scientific SQLite schema discovery, data-dictionary field grounding, cross-table JOIN cardinality control, SQL error self-diagnosis, empty-result triage, and independent Python reconciliation** under the Harbor runtime contract:

- **Skill location**: `/skills/weekly-skill` (scripts under `/skills/weekly-skill/scripts`, references under `/skills/weekly-skill/references`).
- **Case inputs**: `/input/case.json`, `/input/task.md`, authorized SQLite database snapshots (`.sqlite`, `.db`, `.sqlite3`), plus accompanying data dictionaries or metadata files. Always inspect the live database file and dictionary before writing a single SQL statement.
- **Working & output directories**: Stage schema maps, query specs, and intermediate checks under `/app/work`. Deliver verified query CSVs, reconciliation reports, visuals, and `/app/output/result.json` (preserving all original `output_contract` fields and `case_id`) under `/app/output`.
- **Execution environment**: **Scientific Python + SQLite only** (Python 3.12 standard library `sqlite3`, `csv`, `json`, `math`, `statistics` always available; optional `pandas`/`matplotlib` detected dynamically). Do **not** assume Office, PDF/LaTeX renderers, OCR tools, or internet access. Both core scripts (`sqlite_schema_inspector.py` and `sql_cross_table_runner.py`) run 100% natively in pure Python 3.12 stdlib with zero third-party dependencies, and optionally append Matplotlib PNG/PDF previews only when Matplotlib is installed.
- **Resource budget**: 64 model requests, 900 seconds wall-clock, 16,384 tokens per response including reasoning. Save honest partial outputs early and iterate incrementally.
- **Strict immutability**: Input databases are **read-only scientific snapshots**. Every connection MUST open with `file:<resolved_path>?mode=ro` (`uri=True`) plus `PRAGMA query_only = ON`, and verify identical pre- and post-execution SHA-256 database hashes.

## 2. High-Frequency Scientific Database Failure Modes Prevented

SQL queries on scientific datasets frequently succeed syntactically (`0 errors`) while returning scientifically invalid conclusions. This skill directly blocks six recurring failure modes:

1. **Silent 1-to-N JOIN Row Inflation & Uneven Weighting**:
   - Joining a sample-level base table (e.g., `wine_samples`, 178 unique samples) directly to a multi-injection/replicate child table (e.g., `hplc_injections`, 356 rows with 1-3 runs per sample) doubles the row count and weights samples with 3 runs 3x heavier in `AVG(alcohol_pct)` or `SUM(proline_mg_l)`.
   - **Rule**: Always inspect inferred inter-table cardinality (`1:1` vs `1:N`) first. Before joining a `1:N` child table into a base-level summary, **pre-aggregate the child table in a CTE grouped by the join key** (`GROUP BY sample_code`), then `LEFT JOIN` the 1-row-per-key CTE back to the base table.

2. **Coded-Integer Mis-Aggregation**:
   - Integer columns such as `cultivar_code` (`1, 2, 3, 4`), `species_code` (`0, 1, 2`), `qc_pass` (`0, 1`), `status_code`, or `batch_id` represent nominal/ordinal codes or boolean flags, never continuous physical measurements.
   - **Rule**: Never apply `AVG()` or `SUM()` to coded identifier columns (except explicitly documented pass-rate proportions on verified binary 0/1 indicators such as `AVG(CAST(qc_pass AS REAL))` labeled clearly as a rate, not a continuous mean).

3. **Leading-Zero Text ID Corruption**:
   - Sample identifiers such as `'0001'`, `'001'`, `'00123'` stored as `TEXT` lose leading zeros if cast to `INTEGER` (`CAST(sample_code AS INT)`) or loaded blindly into untyped spreadsheets/DataFrames, breaking cross-table joins or silently collapsing distinct codes.
   - **Rule**: Preserve leading-zero IDs strictly as `TEXT` in SQL join predicates, CSV exports, and Python reconciliation keys.

4. **Schema Constraint Erosion (`sqlite-utils` / Import Copies)**:
   - Rebuilt or copied SQLite snapshots (for instance via `sqlite-utils` transforms or raw CSV imports) frequently drop `FOREIGN KEY`, `NOT NULL`, `UNIQUE`, or `CHECK` constraints without warning.
   - **Rule**: Never trust missing `PRAGMA foreign_key_list` entries as proof of table independence or uniqueness. `sqlite_schema_inspector.py` verifies live primary-key uniqueness, exact duplicate rows, null/empty-string counts, orphan foreign keys, and name-matched implicit join edges directly against the data.

5. **Conflating Legitimate Empty Dimension Groups with Anomalous Empty Queries**:
   - A `LEFT JOIN` from a complete reference/dimension table (e.g., `cultivars` containing a control group `Nebbiolo_Control_Empty` with `0` measured samples in `wine_samples`) legitimately produces a group row with `n_samples = 0` and `avg_metric = NULL` (`LEGITIMATE_EMPTY_GROUP`).
   - By contrast, a query returning `0` total rows due to a misspelled literal, case-sensitive filter mismatch, broken join predicate, or impossible `WHERE` clause is an `ANOMALOUS_EMPTY_RESULT`.
   - **Rule**: Never impute `NULL` averages of empty groups to `0.0`, and never silently accept a 0-row query without running diagnostic table/predicate probes.

6. **Unreconciled SQL Summaries & Opaque Errors**:
   - Every key cross-table aggregate is independently recalculated in native Python from raw table rows and checked for `max_abs_diff < 1e-9` across all groups (including empty groups).
   - When a SQL query fails (`OperationalError`), capture the raw error, run schema-aware fuzzy column/table matching, log exact repair suggestions, and cap automated repair retries cleanly.

## 3. Standard Six-Stage Execution Pipeline

Run every database analysis case through these six auditable stages:

```bash
SCRIPTS=/skills/weekly-skill/scripts

# Stage 0: Harbor preflight, budget ledger, result scaffold, and input indexing
python3 -B "$SCRIPTS/preflight_probe.py" --input /input --output /app/output --out /app/work/env_probe.json
python3 -B "$SCRIPTS/budget_guard.py" --state /app/work/.budget_state.json --init
python3 -B "$SCRIPTS/result_guard.py" --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "challenge-5c56458d"
python3 -B "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# Stage 1: Strictly read-only schema, PK/FK, orphan, coded-column, leading-zero & cardinality audit
python3 -B "$SCRIPTS/sqlite_schema_inspector.py" \
  --db /input/<DATABASE_FILE>.sqlite \
  --out /app/work/db_schema_audit.json \
  --md-out /app/work/db_schema_audit.md

# Stage 2: Author parameterized read-only query spec (/app/work/queries.json)
#          - Ground all table/column names in db_schema_audit.json + /input data dictionary
#          - Pre-aggregate any 1:N child tables in CTEs before joining to base_table
#          - Attach independent Python reconciliation block for primary aggregates
python3 -B "$SCRIPTS/budget_guard.py" --state /app/work/.budget_state.json --tick "design_queries"

# Stage 3: Execute read-only SQL + EXPLAIN QUERY PLAN + JOIN inflation guard +
#          empty-group triage + native Python reconciliation (<1e-9) + SVG/optional PNG chart
python3 -B "$SCRIPTS/sql_cross_table_runner.py" \
  --db /input/<DATABASE_FILE>.sqlite \
  --query-spec /app/work/queries.json \
  --out-dir /app/output \
  --stem sql_analysis

# Stage 4: Seal result.json preserving all original output_contract fields and run final gates
python3 -B "$SCRIPTS/result_guard.py" \
  --path /app/output/result.json \
  --patch-json '{"artifacts":["sql_analysis_report.json","sql_analysis_report.md","sql_analysis_chart.svg"]}' \
  --finalize
python3 -B "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B "$SCRIPTS/final_audit.py" --dir /app/output \
  --require sql_analysis_report.json \
  --require sql_analysis_report.md \
  --require sql_analysis_chart.svg
```

### Stage-by-Stage Evidence & Stopping Criteria

| Stage | Tool | Mandatory Evidence | Hard Stop / Escalation Condition |
|---|---|---|---|
| 0. Grounding | `preflight_probe.py`, `block_indexer.py` | Verified `/input` DB path, case instructions, and data dictionary text | Missing DB file or unreadable input -> record `BLOCKED` gap immediately |
| 1. Schema Audit | `sqlite_schema_inspector.py` | `db_schema_audit.json`: `db_immutability_verified: true`, PK uniqueness, exact duplicates, orphan FKs, `coded_integer_columns`, `leading_zero_text_columns`, `relationship_cardinalities` | Corrupt SQLite file or duplicate PK in dimension table -> halt joins until deduplication rule is documented |
| 2. Query Design | `/app/work/queries.json` | Read-only `SELECT`/`WITH` SQL with pre-aggregated CTEs for `1:N` relations, `LEFT JOIN` where empty groups matter, explicit `reconcile` config | Unresolvable semantic ambiguity between two columns -> document in `uncertainty` |
| 3. Execution & Guard | `sql_cross_table_runner.py` | `EXPLAIN QUERY PLAN`, `join_inflation_detected == false`, `empty_set_classification`, `python_reconciliation.passed == true` (`max_abs_diff < 1e-9`), CSV exports, SVG chart | Write SQL rejected, JOIN inflation triggered, `ANOMALOUS_EMPTY_RESULT`, or reconciliation diff `>= 1e-9` -> repair SQL (max 2 rounds) |
| 4. Final Audit | `result_guard.py`, `gap_gate.py`, `final_audit.py` | Valid `/app/output/result.json`, zero ungrounded claims, DB SHA-256 provenance recorded | Any audit failure -> repair or downgrade `status` honestly to `partial`/`failed` |

## 4. Read-Only Safety Fence & SQL Error Self-Localization

`sql_cross_table_runner.py` enforces a two-layer read-only guarantee:
1. **Engine layer**: SQLite URI `file:<abs_db>?mode=ro` + `PRAGMA query_only = ON` + pre/post DB SHA-256 verification.
2. **Parser fence**: Strips SQL comments and string literals, rejects stacked statements (`;`), requires `SELECT` or `WITH` prefix, and blocks `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `REPLACE`, `ATTACH`, `DETACH`, `VACUUM`, `REINDEX`, `PRAGMA`, `BEGIN`, `COMMIT`, `ROLLBACK`.

When a read-only query raises `sqlite3.OperationalError`:
- The runner preserves the raw SQLite error verbatim.
- It inspects the error text (`no such column: X`, `no such table: Y`, `ambiguous column name: Z`) against live `sqlite_master` / `PRAGMA table_info` metadata and computes `difflib.get_close_matches` repair suggestions (`self_diagnosis`).
- Limit automated SQL repair iterations to **2 rounds per query**; if a required table or column genuinely does not exist in the snapshot, stop and report the exact gap rather than substituting an unrelated column.

## 5. Empty-Group Triage & Visual Reporting Semantics

- **`LEGITIMATE_EMPTY_GROUP`**: Detected when a query returns `>= 1` rows and one or more dimension groups have `n_samples == 0` (typically from `LEFT JOIN` on a complete dimension table) with `NULL` mean/std/min/max. These groups are scientifically meaningful (e.g., unmeasured control cultivar or zero-enrollment stratum): keep them visible in CSV, JSON, Markdown, and charts with explicit `n=0 (empty)` labeling — never impute `NULL` means to `0.0` and never silently drop them via `INNER JOIN` unless requested.
- **`ANOMALOUS_EMPTY_RESULT`**: Detected when a query returns `0` rows unexpectedly. The runner automatically checks whether the referenced tables contain rows and inspects literal equality predicates in `WHERE` to identify mismatched case/code values.
- **`EXPECTED_EMPTY_AUDIT`**: Triggered only when a diagnostic/QC query explicitly sets `"allow_empty_result": true` (for example, checking that zero orphan foreign-key records exist).
- **Visual output**: `sql_cross_table_runner.py` always emits a crisp, self-contained, editable vector SVG (`<stem>_chart.svg`) using pure Python standard library (zero Matplotlib dependency required), rendering `n=0` empty groups with a clean dashed empty-group indicator. If Matplotlib is genuinely installed in the container, it additionally exports 300-dpi `<stem>_chart.png` and vector `<stem>_chart.pdf`.

## 6. Query Specification Schema & CLI Quick Reference

### CLI Reference for Bundled SQLite Tools

| Script | Required Flags | Optional Flags | Key Verified Guarantees |
|---|---|---|---|
| `sqlite_schema_inspector.py` | `--db <sqlite>` | `--out <json>`, `--md-out <md>` | `mode=ro` + `PRAGMA query_only=ON`, pre/post SHA-256 immutability, PK uniqueness, exact duplicate count, FK orphan count, zero-child parent keys, coded-integer flags, leading-zero TEXT ID detection, automatic `1:1`/`1:N` cardinality inference |
| `sql_cross_table_runner.py` | `--db <sqlite>`, `--query-spec <json>`, `--out-dir <dir>` | `--stem <name>` | Read-only SQL fence, `EXPLAIN QUERY PLAN`, fuzzy SQL error self-diagnosis, detail & aggregate JOIN inflation guard + CTE hint, coded-column mis-aggregation check, empty-group vs anomalous-empty triage, native Python recalculation (`max_abs_diff < 1e-9`), stdlib SVG (+ optional Matplotlib PNG/PDF) |

### Canonical Safe Pre-Aggregation CTE Pattern

Always pre-aggregate `1:N` child tables to **one row per parent key** before joining back to the base sample table and dimension table:

```sql
WITH child_per_sample AS (
    SELECT
        sample_code,
        COUNT(*) AS n_injections,
        AVG(CAST(qc_pass AS REAL)) AS sample_qc_pass_rate
    FROM hplc_injections
    GROUP BY sample_code
)
SELECT
    c.cultivar_code,
    c.cultivar_label,
    COUNT(w.sample_code) AS n_samples,
    AVG(w.alcohol_pct) AS mean_alcohol_pct,
    COALESCE(SUM(h.n_injections), 0) AS total_injections,
    AVG(h.sample_qc_pass_rate) AS mean_sample_qc_pass_rate
FROM cultivars c
LEFT JOIN wine_samples w ON c.cultivar_code = w.cultivar_code
LEFT JOIN child_per_sample h ON w.sample_code = h.sample_code
GROUP BY c.cultivar_code, c.cultivar_label
ORDER BY c.cultivar_code;
```

```sql
-- Pattern B: Single-Table Continuous Variable Stratification / Binning (e.g., Concrete curing age strata)
WITH binned AS (
    SELECT
        mix_id,
        CASE
            WHEN age_days <= 7 THEN '01_early_le_7d'
            WHEN age_days <= 28 THEN '02_standard_8_28d'
            ELSE '03_mature_gt_28d'
        END AS age_stratum,
        cement_kg_m3,
        water_kg_m3,
        water_kg_m3 / NULLIF(cement_kg_m3, 0.0) AS water_cement_ratio,
        compressive_strength_mpa
    FROM concrete_mixes
)
SELECT
    age_stratum,
    COUNT(*) AS n_samples,
    ROUND(AVG(compressive_strength_mpa), 4) AS mean_strength_mpa,
    ROUND(AVG(water_cement_ratio), 4) AS mean_wc_ratio
FROM binned
GROUP BY age_stratum
ORDER BY age_stratum;
```

### Zero-Manual One-Command Entrypoint (`auto_sqlite_analyzer.py`)

For any `/input` directory containing a SQLite database (multi-table 1:N star schema like Wine/HPLC or normalized `studies -> patients -> lab_assays`, single-table categorical like Iris, or continuous single-table like Concrete 1030), run the zero-manual orchestrator to automatically inspect schema, parse parameterized `WHERE` / `ORDER BY` / `LIMIT` directives from `task.md` (`?` parameter binding), synthesize inflation-free CTE / stratified SQL + mirrored Python reconciliation specs, execute read-only, verify `max_abs_diff < 1e-9`, compute exact pure-stdlib One-Way ANOVA / Welch ANOVA / Kruskal-Wallis $p$-values + pairwise Welch $t$ (Holm-Bonferroni + Hedges' $g$) + automatic Wilson score 95% CIs for binary 0/1 proportions (`ci_method: "wilson_score_95"`), and seal `/app/output/result.json`:

```bash
python3 "$SCRIPTS/auto_sqlite_analyzer.py" --input-dir /input --out-dir /app/output --stem sql_analysis
```

### Independent Python Reconciliation & Output Contract Sealing

Include a `reconcile` dictionary inside each primary aggregate query so `sql_cross_table_runner.py` independently recalculates every group from raw base tables in pure Python and asserts:
1. Exact group-key set equality (catching accidental `INNER JOIN` drops of empty groups);
2. Exact integer sample-count equality per group;
3. Strict `NULL` vs `0.0` parity (a `NULL` mean on an empty group never matches `0.0`);
4. Numeric agreement `abs(sql_value - python_value) < 1e-9`.

Seal `/app/output/result.json` with `case_contract_sealer.py seal --case-json /input/case.json --result-json /app/output/result.json --task-id challenge-5c56458d --payload-json /app/output/sql_analysis_report.json --artifacts "sql_analysis_report.json,sql_analysis_report.md,sql_analysis_chart.svg"` so every field in `case.json`'s `output_contract` is preserved deterministically.

## 7. Required Output Contract & Pre-Submission Checklist

Before calling `final_audit.py`, verify `/app/output/result.json` includes:
- `case_id` matching `/input/case.json` and every original `output_contract` field from `task.md`.
- `status` (`"ok"`, `"partial"`, or `"failed"`), honest `gaps` / `uncertainty`, and `change_records` for any repaired SQL.
- `sources` recording the exact input `.sqlite` filename, `db_sha256_before == db_sha256_after`, `db_immutability_verified: true`, and SQLite version.
- `metrics` and `metric_provenance` linking every summary figure to its query ID, SQL statement, CSV artifact, and `python_reconciliation.max_abs_diff`.
- `artifacts` listing `sql_analysis_report.json`, `sql_analysis_report.md`, `sql_analysis_chart.svg`, and exported per-query `.csv` files (never `.tsv`).

**Five-Point Final Sanity Check**:
1. Zero write/DDL statements executed and `db_immutability_verified == true`.
2. Zero `join_inflation_detected == true` flags remaining on final delivered queries.
3. Zero `coded_misaggregation_warnings` (no `AVG(cultivar_code)` or `SUM(species_code)`).
4. All `LEGITIMATE_EMPTY_GROUP` rows preserved transparently with `NULL` means; zero unresolved `ANOMALOUS_EMPTY_RESULT` queries.
5. All primary aggregates verified via independent Python recalculation (`max_abs_diff < 1e-9`), and `gap_gate.py` + `final_audit.py` exit `0`.

## 8. Reference Library & Examples

Consult these bundled references for exact SQL patterns and edge-case rules:
- `references/readonly-schema-and-coded-columns.md` — Read-only connection protocol, `sqlite-utils` constraint-loss checks, coded-integer heuristics, and leading-zero ID preservation.
- `references/join-inflation-guard-and-cte-patterns.md` — Diagnosing 1:N JOIN inflation, uneven replicate weighting, and canonical pre-aggregation CTE templates.
- `references/empty-group-triage-and-reconciliation.md` — Distinguishing `LEGITIMATE_EMPTY_GROUP` vs `ANOMALOUS_EMPTY_RESULT`, `NULL` vs `0` reporting, and independent Python recalculation (`max_abs_diff < 1e-9`).
- `examples/example_query_spec.json`, `examples/example_queries.sql`, `examples/example_workflow_trace.md` — Runnable query spec and illustrative trace (marked as examples only).

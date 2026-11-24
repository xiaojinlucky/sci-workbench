---
name: multistep-scientific-data-agent
description: >-
  Use when orchestrating an end-to-end, checkpointed multi-step scientific data
  analysis workflow across CSV, JSON, or read-only SQLite snapshots (such as
  UCI Concrete, Iris, Wine, and experimental assay tables). Runs five resumable
  stages (S1_intake -> S2_clean -> S3_stats -> S4_viz -> S5_report) with disk
  SHA-256 checkpoints, verified resume after interruption, coded-integer and
  leading-zero ID protection, assumption-checked statistics (Welch ANOVA,
  Kruskal-Wallis, Hedges' g, Cliff's delta, Holm-Bonferroni), Cell-styled
  native SVG/PNG/PDF charts, and single-source report backfill under /app/output.
---

# Multi-Step Scientific Data Analysis Agent

Skill resources live under `/skills/weekly-skill`. Case inputs live under `/input/` (`case.json`, `task.md`, raw tables, data dictionaries, or SQLite `.db`/`.sqlite` files). Final deliverables live under `/app/output/`, including `result.json` preserving `case_id` and all original `output_contract` fields.

**Environment & Resilience Contract**:
- **Scientific Python + SQLite ONLY**: Never invoke external Office/PDF/OCR renderers.
- **Dual-Backend Zero-Crash Execution**: Core scripts (`dataset_intake_profiler.py`, `multistep_pipeline_engine.py`) run natively on pure Python 3.12 stdlib (including Welch ANOVA, Kruskal-Wallis, Mann-Whitney U, Holm-Bonferroni, and Cell-styled vector SVG rendering) and automatically upgrade to SciPy exact distributions and Matplotlib 300 dpi PNG/PDF exports when installed.
- **Checkpoint Integrity (No Unpersisted Success)**: Every stage persists its artifact files and SHA-256 checksums into `<stem>_checkpoint.json` before advancing. In-memory text or half-written outputs are **never** treated as completed deliverables; any stage failure stops downstream stages and remains explicitly visible in both the checkpoint and `result.json`.

---

## 1. Five-Stage Orchestration & Decision Tree

Follow `S1_intake -> S2_clean -> S3_stats -> S4_viz -> S5_report` sequentially. Lock the scientific question, inference unit, and stopping conditions before execution.

### Stage 1 (`S1_intake`) — Read-Only Schema, Role Inference & Field Alignment
Run `dataset_intake_profiler.py` (or Stage 1 inside `multistep_pipeline_engine.py`):
- Auto-discover `/input` files via `case_contract_sealer.py discover` (or `--input-dir /input`), reading `/input/case.json`, `task.md`, and any delivered data dictionary before choosing columns.
- Load CSV/JSON/SQLite strictly read-only (`file:<db>?mode=ro`) as raw strings so leading zeros (`0001`, `00123`) are preserved verbatim.
- Record raw input SHA-256, row/column dimensions, exact duplicate count, missingness rates, zero-variance columns, and IQR outlier counts.
- **Automatic Column-Role & Mode Routing (`infer_column_roles`)**:
  - When `--group-col` and `--value-col` are omitted, automatically inspect schema and cardinality:
  - Tables with a non-ID categorical grouping column (`2 <= unique <= 12`, e.g., Iris `species`, Wine `cultivar_name`) route to `group_comparison`.
  - Purely continuous multivariable tables without natural groups (e.g., UCI Concrete 1030) automatically route to `continuous_multivariate` (descriptive summaries + Pearson/Spearman correlation matrix + pure-stdlib multivariable OLS regression with 95% CIs, F-test, and Jarque-Bera residual diagnostics + Observed-vs-Predicted SVG plot).
- **Coded-Integer Blocker**: Automatically detect low-cardinality integer identifier/category columns (`cultivar_code`, `species_code`, `status`, `batch_id`). **Refuse** to run continuous mean/ANOVA aggregation on a coded categorical column unless overridden by an explicit data dictionary.
- Persist `<stem>_s1_intake.json` + SHA-256 into `<stem>_checkpoint.json`.

### Stage 2 (`S2_clean`) — Rule-Logged Non-Silent Cleaning
- Apply explicit, ordered cleaning steps recorded in `<stem>_s2_ledger.json`:
  1. `raw_intake_snapshot` (immutable raw count)
  2. `deduplicate_exact` (keep earliest occurrence, log exact dropped count)
  3. `filter_missing_analysis_fields` (explicitly drop missing/sentinel/unparseable rows on the active analysis columns; **zero silent imputation**, `imputation_performed: false`)
- Verify row conservation (`rows_raw == rows_cleaned + exact_duplicates + excluded_missing_or_unparseable`) and persist `<stem>_s2_cleaned.csv` + `<stem>_s2_ledger.json` + SHA-256.

### Stage 3 (`S3_stats`) — Assumption-Verified Statistical Inference
- Inspect every group before selecting an inference method:
  - Normality: Shapiro-Wilk (when SciPy available and `3 <= n <= 5000`) or pure-stdlib Jarque-Bera screen. Groups with `n < 3` are marked `underpowered_n_lt_3`; constant groups are marked `zero_variance_degenerate`.
  - Variance homogeneity: Brown-Forsythe median-centered Levene test.
- **Automatic Route Selection**:
  - All groups pass normality & equal variance -> `one_way_ANOVA` + `Student_t` + `eta_squared`.
  - All groups pass normality & unequal variance -> **exact `Welch_ANOVA`** (never equal-variance ANOVA!) + `Welch_t` (Welch-Satterthwaite df) + 95% difference CI.
  - Any group non-normal, `n < 3`, or zero-variance -> **`Kruskal_Wallis`** (`epsilon_squared`) + **`Mann_Whitney_U`** + **Hodges-Lehmann median shift** + **Cliff's delta** (never rely on parametric mean diffs alone!).
- Always compute **both** parametric (`Hedges' g`, 95% CI) and non-parametric (`Hodges-Lehmann shift`, `Cliff's delta`) effect sizes alongside **Holm-Bonferroni adjusted `p_holm`**.
- Persist `<stem>_s3_stats.json` + SHA-256.

### Stage 4 (`S4_viz`) — Cell-Styled Native Visualization
- Render publication-ready vector SVG (`114 x 82 mm` default Cell 1.5-column preset) with:
  - Panel-centered title (`loc="center"`), low-saturation Cell palette (`#547FA3`, `#D77C70`, `#4E9187`, `#D19A45`, `#967A9C`, `#66A9B4`), white background, no top/right spines.
  - Bars/summary + 95% CI error bars + jittered raw replicate points + per-group `n` labels.
  - **Signed-value & full-range integrity**: If any observation or CI bound is negative, draw an explicit `y = 0` reference line and verify `y_axis_limits` covers `[min(all_points, ci_low), max(all_points, ci_high)]` with `value_range_covered: true`.
- When Matplotlib is installed, simultaneously export 300 dpi `.png` and editable vector `.pdf` (`pdf.fonttype=42`, `bbox_inches=None`).
- Persist chart files + `<stem>_s4_viz_audit.json` + SHA-256.

### Stage 5 (`S5_report`) — Single-Source Backfill & Reconciliation
- Re-read `<stem>_s2_cleaned.csv` from disk and verify exact numeric identity across `disk_cleaned_rows == stats.rows_analyzed == ledger.rows_cleaned`.
- Populate `<stem>.md` **exclusively** from persisted `S1..S4` JSON metrics (zero manual prose re-typing that could drift from tables/charts).
- Present findings in **Result -> Methodological Context -> Boundary** order using clean domain language (no internal variable/filter-branch leakage and no bloated boilerplate disclaimers; state only limits that genuinely affect interpretation).
- Emit linear imperative reproduction script `reproduce_<stem>.py` and `<stem>_reconciliation.json`.

---

## 2. Interruption Recovery & Checkpoint Contract (`--resume`)

Real multi-step workflows can be interrupted by timeouts, bad columns, or upstream data updates. Our checkpoint protocol guarantees safe handoff:
1. `<stem>_checkpoint.json` records `case_id`, `input_sha256`, `group_col`, `value_col`, `overall_status`, and per-stage `{status, started_at, completed_at/failed_at, artifacts: {filename: sha256}, error}`.
2. When invoked with `--resume`:
   - If `input_sha256` or analysis columns changed, **invalidate all prior checkpoints** and recompute cleanly from `S1_intake`.
   - For each stage marked `COMPLETED`, re-hash every listed artifact on disk. Only skip the stage if **every file exists and matches its recorded SHA-256**.
   - Resume execution from the first unverified, `PENDING`, or `FAILED` stage.
3. If any stage fails:
   - Record `status = "FAILED"` and the exact exception text in `<stem>_checkpoint.json`.
   - Set `overall_status = "FAILED_AT_<stage>"` and exit non-zero (`rc = 1`).
   - Never leave a stale success report pretending the pipeline succeeded.

---

## 3. Anti-Failure Checklist & Edge Cases

| Edge Case | Deterministic Handling |
| --- | --- |
| Missing column or wrong table name | Fail loudly at `S1_intake` listing available columns/tables; record `FAILED_AT_S1_intake` |
| Coded integer passed as `value_col` | Blocked at `S1_intake` (`coded_integer_warnings`) to prevent averaging category IDs |
| Empty table / 0 rows after cleaning | Fail loudly at `S2_clean` with explicit row-drop ledger |
| Group with `n < 3` or zero variance | Do not crash on Shapiro/Levene/Welch; automatically route to Kruskal-Wallis + Cliff's delta + explicit warning |
| Negative / signed measurements | Draw `y = 0` line and expand y-axis to cover negative data and CI whiskers |
| Corrupted or truncated stage artifact | `--resume` detects SHA-256 mismatch and re-runs that stage and all downstream stages |
| SQLite read-only / permission boundary | Open strictly with `file:<db>?mode=ro` URI; never write into source database |

---

## 4. Standard Execution Pipeline

```bash
SCRIPTS=/skills/weekly-skill/scripts

# 1. Preflight, budget, dynamic /input discovery, and input inventory
python3 "$SCRIPTS/preflight_probe.py" --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --init
python3 "$SCRIPTS/case_contract_sealer.py" discover --input-dir /input --out /app/work/case_discovery.json
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# 2. Execute checkpointed 5-stage analysis pipeline (auto-discovers /input table, auto-routes grouped vs continuous OLS, and auto-seals case.json output_contract into result.json)
python3 "$SCRIPTS/multistep_pipeline_engine.py" \
  --input-dir /input \
  --case-json /input/case.json \
  --out-dir /app/output \
  --stem analysis_report
# Explicit column override when needed: add --group-col <col> --value-col <col> [--table <tbl>] [--resume]

# 3. Validate sealed result.json against /input corpus and final_audit
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --validate
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" \
  --dir /app/output \
  --require analysis_report_checkpoint.json \
  --require analysis_report_s3_stats.json \
  --require analysis_report.md \
  --require analysis_report_chart.svg
```

## 5. Reference Modules

- `references/checkpoint_and_recovery_protocol.md`: Stage state machine, SHA-256 verification rules, and interruption handoff contract.
- `references/stats_and_chart_rules.md`: Assumption decision tree, Welch/Kruskal routing, effect sizes, and Cell-styled SVG/Matplotlib visual invariants.
- `references/report_backfill_and_boundaries.md`: Single-source numeric reconciliation and scientific presentation standards.

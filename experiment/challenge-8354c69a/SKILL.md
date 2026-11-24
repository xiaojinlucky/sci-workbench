---
name: experimental-result-anomaly-analyst
description: >-
  Multi-modality experimental result interpreter and anomaly analyst for batch
  raw traces (e.g. bending curves), multi-sheet Excel workbooks (e.g. 12-sheet
  assays), MATLAB/Python scripts, and multi-split computational experiments.
  Separates missing/BLQ/explicit-zero/saturated readings, isolates batch shifts
  from point outliers, audits paired splits, validates 5-part causal reports,
  and retains source unit/method/threshold ambiguities into /app/output.
---

# Experimental Result Interpretation & Anomaly Analysis (`challenge-8354c69a`)

Interpret heterogeneous real experimental artifacts — batch raw instrument curves/traces, multi-sheet `.xlsx` workbooks, MATLAB `.m` or Python scripts, computational multi-split/multi-estimator tables, and manuscripts — into a reproducible **5-part scientific explanation**: **(1) Result Trends, (2) Stratified Anomalies, (3) Ranked Causal Hypotheses, (4) Executable Verification Checklist, and (5) Next-Experiment Recommendations**, while preserving every source ambiguity and never fabricating measurements, replicates, or root-cause confirmations.

## Runtime & Budget Contract

- **Skill root**: `/skills/weekly-skill` (scripts under `/skills/weekly-skill/scripts`, references under `/skills/weekly-skill/references`).
- **Inputs**: Inspect `/input/case.json`, `/input/task.md`, all `.xlsx` sheets, raw curve `.csv`/`.tsv`/`.dat`/`.txt` traces, `.m` MATLAB scripts, and accompanying manuscripts under `/input/`. Never invent missing wet-lab replicates or unrecorded causes.
- **Working directory**: `/app/work` (`env.json`, `.budget_state.json`, `blocks.json`, `trace_workbook_profile.json`, `outlier_report.json`, `split_summary.json`, `batch_trace_anomalies.json`, `5part_audit.json`, `gaps.json`).
- **Deliverables**: `/app/output/interpretation_report.json`, `/app/output/interpretation_report.md` (plus any exact filename required by `/input/case.json`), and `/app/output/result.json` preserving `case_id` and every `output_contract` field.
- **Execution budget**: 64 requests / 900 s / 16,384 output tokens per turn. Initialize `preflight_probe.py`, `budget_guard.py`, and `result_guard.py` on Turn 1.

## Four-Stage Multi-Modality Diagnostic Workflow

### Stage 1: Full-Coverage Profiling & Cross-Source Ambiguity Scan (`multi_sheet_and_trace_profiler.py`)
Run `multi_sheet_and_trace_profiler.py --input /input --out /app/work/trace_workbook_profile.json` FIRST to profile every artifact without dropping sheets or traces:
- **Multi-Sheet Workbooks (`.xlsx`, e.g., 12-sheet analytical assays)**: Uses `openpyxl` when available and falls back automatically to pure-stdlib `zipfile + xml` parsing so every sheet is inspected even in slim offline containers. Automatically distinguishes preamble metadata rows from tabular headers, counts `numeric`, `explicit_zero`, `missing`, `below_lod` (`<LOD`/`BLQ`/`ND`), and `non_numeric_other` cells separately, and extracts unit/method/threshold tokens.
- **Batch Raw Curve Traces (e.g., 197 bending/load-displacement or kinetics files)**: Computes per-trace `peak_y`, `x_at_peak`, `initial_slope`, `auc_trapezoid`, `inflection_count`, `post_peak_drop_pct`, `plateau_tail_cv`, `saturation_flag`, `truncated_flag`, and `flatline_flag`, plus population-level trace outliers.
- **MATLAB `.m` & Text Parameter Scan**: Extracts parameter/threshold assignments and comments, then cross-checks units and thresholds across workbooks, traces, scripts, and papers to populate `detected_ambiguities`.

### Stage 2: Stratified Outliers, Batch Shifts & Paired Split Audits (`outlier_and_split_detector.py`)
Choose all subcommands matching the case's data modalities:
1. **Tabular / Workbook Readings (`outliers`)**: Works on both `.csv` and `.xlsx --sheet <SHEET_NAME>`.
   - Computes group/batch/channel/well-stratified **IQR (1.5x)** and **Modified Z-score (MAD-Z)** outlier fences.
   - Separates `explicit_zero_count` from `data_gaps` (`missing`, `below_lod`, `invalid_or_non_finite`, `saturated`).
   - Reports `analysis_status`: `complete`, `partial` (numeric results exist but gaps excluded), or `no_valid_numeric_readings` (failed analysis — NEVER reported as "0 outliers"!).
   - Runs **Between-Batch / Between-Channel Shift Analysis** (one-way F-statistic and between-group median spread ratio) to separate systematic batch/instrument drift from isolated single-sample outliers.
2. **Computational Multi-Split / Multi-Estimator Experiments (`splits`)**:
   - Computes per-estimator mean, SD, median, IQR, min/max, and missing-split gaps.
   - Computes **paired split-by-split differences** across shared splits (`mean_delta`, `sd_delta`, 95% CI, paired `t_stat`, exact `wins/ties/losses`), decomposes `between_split_variance` vs `between_estimator_variance`, and flags anomalous hard splits.
3. **Batch Trace Population Anomalies (`batch-traces`)**:
   - Consumes `trace_workbook_profile.json` and runs prefix/group-stratified IQR/MAD fencing across trace metrics (`peak_y`, `initial_slope`, `auc_trapezoid`, `points`, `plateau_tail_cv`).

### Stage 3: Five-Part Scientific Interpretation & Four-Layer Attribution
Synthesize `/app/output/interpretation_report.json` (and human-readable `/app/output/interpretation_report.md`) covering all 5 required sections + ambiguity/gap ledgers + 6-element traceability:
1. **`trends`**: Direction, inflection/yield points, plateau stability, dispersion, and exact interval/baseline/fitting rule cited to file/sheet/column — never mere readout repetition.
2. **`anomalies`**: Exact `source_file`, `location` (row/sheet/trace/split), `target_id`, `dimension` (`sample`/`batch`/`channel`/`well`/`timepoint`/`split`), `observed_value`, `reference_baseline`, `threshold_rule`, and `deviation_magnitude`. Distinguish isolated point anomalies from systematic batch shifts.
3. **`causal_hypotheses`**: Ranked by evidence strength and testability across four diagnostic layers — **(a) Operator/protocol deviation (ELN deviation log), (b) Batch/channel/plate-position effect, (c) Instrument saturation/calibration or reagent failure, (d) Genuine physical/biological/algorithmic mechanism**. Every hypothesis MUST state `supporting_evidence`, `excluding_evidence`, `correlation_vs_causation_caveat`, and `falsification_test`.
4. **`verification_checklist`**: Concrete, second-person-executable actions citing exact `target_file_or_log`, `action`, and `decision_rule` — NEVER vague wording like `"检查数据质量"` or `"verify results"`.
5. **`next_experiments`**: Targeted next-round actions mapped directly to unresolved causal hypotheses or evidence gaps (e.g., add concurrent blank/QC control, expand biological replicates, refine sampling near inflection, verify calibration curve).
6. **`retained_ambiguities` & `data_gaps_summary` & `six_element_traceability`**: Retain unresolved source unit, method, or QC threshold discrepancies explicitly instead of silently guessing one; summarize missing/BLQ/saturated counts separately from explicit zeros; trace conclusions back to hypotheses/variables, controls, equipment/material batches, time/cost, parameter ranges, and risks.

### Stage 4: Structural, Grounding & Anti-Boilerplate Audit (`explanation_5part_auditor.py`)
Run `explanation_5part_auditor.py` to validate completeness, anomaly locators, causal rigor, executable checklist specificity, gap honesty, and render `/app/output/interpretation_report.md`.

## Standard Execution Pipeline

Read `references/input_output_schemas.md` before writing `/app/output/interpretation_report.json`.

```bash
SCRIPTS=/skills/weekly-skill/scripts

# 1. Preflight, budget ledger, skeleton result.json, input index, and full multi-sheet/trace/MATLAB profile
python3 "$SCRIPTS/preflight_probe.py" --input /input --output /app/output --work /app/work --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --state /app/work/.budget_state.json --init
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "challenge-8354c69a"
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/blocks.json
python3 "$SCRIPTS/multi_sheet_and_trace_profiler.py" --input /input --out /app/work/trace_workbook_profile.json

# 2a. Tabular/Workbook stratified outliers + batch-shift analysis (supports .csv and .xlsx --sheet)
python3 "$SCRIPTS/outlier_and_split_detector.py" outliers \
  --file /input/<DATA_FILE> --sheet "<OPTIONAL_SHEET>" \
  --value-col <METRIC> --group-col <BATCH_OR_GROUP> --id-col <SAMPLE_ID> \
  --out /app/work/outlier_report.json

# 2b. Computational multi-split / multi-estimator paired comparison (when applicable)
python3 "$SCRIPTS/outlier_and_split_detector.py" splits \
  --csv /input/<CV_RESULTS.csv> --split-col <SPLIT> --estimator-col <ESTIMATOR> --metric-col <METRIC> \
  --out /app/work/split_summary.json

# 2c. Batch curve/trace population anomaly analysis (when raw trace files exist)
python3 "$SCRIPTS/outlier_and_split_detector.py" batch-traces \
  --profile /app/work/trace_workbook_profile.json \
  --out /app/work/batch_trace_anomalies.json

# 3. Author /app/output/interpretation_report.json, pre-validate schema, audit 5-part rigor & render Markdown
python3 "$SCRIPTS/validate_and_sync_result.py" validate-input \
  --input /app/output/interpretation_report.json --mode 8354c69a --out /app/work/report_schema_audit.json
python3 "$SCRIPTS/explanation_5part_auditor.py" \
  --report /app/output/interpretation_report.json \
  --out /app/work/5part_audit.json \
  --md-out /app/output/interpretation_report.md

# 4. Auto-sync /input/case.json output_contract into /app/output/result.json and run grounding + final audit
python3 "$SCRIPTS/validate_and_sync_result.py" sync-result \
  --case-json /input/case.json \
  --report /app/output/interpretation_report.json \
  --result-json /app/output/result.json \
  --task-id "challenge-8354c69a" \
  --artifact interpretation_report.json --artifact interpretation_report.md
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" --dir /app/output --result-file result.json --require interpretation_report.json --require interpretation_report.md
```

## Non-Negotiable Scientific Honesty Rules

- **Retain Source Ambiguities**: Preserve conflicting units, method definitions, missing baselines, or unspecified QC thresholds under `retained_ambiguities` rather than silently overriding them.
- **Observation != Cause**: Statistical outlier flags never prove root cause; rank competing mechanisms with supporting/excluding evidence and falsification checks.
- **Missing / BLQ != Zero != Zero-Outlier**: Keep missing, non-numeric, `<LOD`/BLQ, and saturated readings explicitly separated from verified `0` readings and from clean zero-outlier claims.
- **No Fabricated Measurements or Replicates**: Report actual observed counts and exact data gaps honestly.

# Multi-Modality Anomaly Isolation & Five-Part Interpretation Protocol

## 1. Semantic Separation of Non-Standard Readings

Never collapse distinct measurement states into a single bucket:
- **Verified Explicit Zero (`0.0`)**: Valid numeric zero reading; preserve in `explicit_zero_count`.
- **Missing / Blank (`""`, `NA`, `null`)**: Absent observation; record under `data_gaps` with `status: "missing"` — NEVER impute as `0`.
- **Below Limit of Detection (`<LOD`, `BLQ`, `ND`)**: Left-censored analytical reading; record under `data_gaps` with `status: "below_lod"`.
- **Sensor Saturation / Over-Range (`>ULOQ`, `OVR`, `>= upper_rail`, flat rail >= 5 pts)**: Right-censored clipping; record as `saturated` rather than true peak.
- **Zero-Valid-Numeric Failure**: When `valid_reading_count == 0`, set `analysis_status: "no_valid_numeric_readings"` (`ok: false`) — NEVER claim "0 outliers found".

## 2. Isolated Point Outlier vs. Systematic Batch/Channel Shift

Always check both levels before blaming a single specimen:
1. **Within-Group Stratified Outlier (`IQR 1.5x` and `|MAD-Z| > 3.5`)**: Isolates individual specimens/rows/traces deviating from their own batch/channel/group median.
2. **Between-Batch / Between-Channel Shift (`one_way_f_stat`, `eta_squared`, `shift_to_within_iqr_ratio`)**: Isolates systematic offsets affecting an entire plate, lot, instrument channel, or day.
3. **Paired Multi-Split Computational Audit**: Always compute paired split-by-split deltas (`A - B`), win/tie/loss counts, and split-difficulty outliers rather than comparing marginal means alone.

## 3. Four-Layer Causal Attribution & ELN Deviation Link

Rank candidate explanations by evidence strength and testability across four layers:
1. `operator_or_protocol_deviation`: Cross-check ELN 4-question deviation logs (planned vs actual setting, timestamp, validity impact, follow-up decision).
2. `batch_channel_or_plate_effect`: Corroborated by between-batch F-ratio/median shift or reagent lot/channel clustering.
3. `instrument_or_reagent_failure`: Corroborated by saturation rails, flatlines, truncated trace length, or blank/QC drift.
4. `true_domain_signal_or_mechanism`: Residual domain effect after ruling out layers 1–3; never claimed as proven causality without orthogonal confirmation.

## 4. Source Ambiguity Retention

When workbooks, MATLAB `.m` scripts, raw trace headers, and manuscripts disagree on units (`MPa` vs `N/mm^2` vs `kPa`, `mg/mL` vs `ug/mL`), baseline subtraction, or outlier thresholds, record each discrepancy verbatim in `retained_ambiguities` instead of silently choosing one.

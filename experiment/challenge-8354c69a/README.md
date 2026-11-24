# Experimental Result Interpretation & Anomaly Analysis (`challenge-8354c69a`)

Offline-native Harbor Skill for reproducible 5-part scientific interpretation across heterogeneous laboratory artifacts: multi-sheet `.xlsx` workbooks (12+ sheets), batch raw curve traces (200+ bending/kinetics files), MATLAB `.m` scripts, and multi-split computational experiments.

## Included Components

- `SKILL.md`: 4-stage multi-modality diagnostic workflow, 4-layer causal attribution discipline, ambiguity retention, and strict missing/BLQ/zero/saturation separation.
- `scripts/multi_sheet_and_trace_profiler.py`: Dual-backend `.xlsx` profiler (`openpyxl` with pure-stdlib `zipfile+XML` fallback), smart preamble-vs-header detection, batch curve mechanics/kinetics profiler (`peak_y`, `initial_slope`, `auc_trapezoid`, `post_peak_drop_pct`, `plateau_tail_cv`, saturation/truncation/flatline flags), MATLAB parameter parser, and cross-source unit/threshold ambiguity detector.
- `scripts/outlier_and_split_detector.py`: Stratified IQR/MAD outlier detector supporting both `.csv` and `.xlsx --sheet`, one-way ANOVA F-stat & median-shift batch-effect isolation, paired multi-split/multi-estimator computational comparator (paired delta, 95% CI, paired t, wins/ties/losses, anomalous splits), and batch-trace population anomaly scanner.
- `scripts/explanation_5part_auditor.py`: Validates 5-part completeness, anomaly locators, 4-layer causal rigor, anti-boilerplate verification actions, gap-status honesty, and renders Markdown summary.
- `scripts/{preflight_probe,budget_guard,result_guard,block_indexer,gap_gate,final_audit}.py`: Deterministic Harbor runtime & grounding gates.

## Dependencies

Python 3.10+ standard library only (automatically uses `openpyxl` if installed, otherwise uses built-in pure-stdlib XLSX XML reader).

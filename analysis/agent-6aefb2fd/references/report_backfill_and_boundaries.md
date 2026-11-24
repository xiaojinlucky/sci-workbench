# Single-Source Report Backfill & Scientific Expression Rules

## 1. Single-Source Numeric Backfill

Every number in `<stem>.md` (raw rows, analyzed rows, group `n`, mean, SD, 95% CI, median, IQR, test statistic, `p_raw`, `p_holm`, Hedges' g, Cliff's delta) MUST be programmatically formatted from `S1..S4` JSON artifacts — never typed freehand.

Before marking `S5_report` as `COMPLETED`, the engine re-reads `<stem>_s2_cleaned.csv` from disk and asserts:
- `disk_cleaned_rows == stats.rows_analyzed == ledger.rows_cleaned`
- `viz_audit.value_range_covered is True`

## 2. Domain Expression vs Internal State Separation

- Lead reports with concise scientific findings, statistical scope, and actionable boundaries.
- Use accurate domain terms (`Group`, `Analyzed observations`, `Holm-adjusted p-value`, `Cliff's delta`) instead of leaking internal pipeline branch names or debugging labels into tables or figure titles.
- Keep technical reproducibility records (SHA-256 digests, stage resume lists, exact commands) in the dedicated provenance section and `reproduce_<stem>.py`.
- Avoid repetitive boilerplate caveats on every paragraph; report only real methodological boundaries that could alter conclusions (e.g., small subgroup `n < 3`, zero variance, non-normality, observational non-causality).

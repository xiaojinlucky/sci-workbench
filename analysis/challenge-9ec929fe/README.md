# Scientific Code Generation, Execution & Verification Skill (`challenge-9ec929fe`)

A self-contained, offline-capable Harbor Skill for generating, environment-locking, executing in isolation, cross-verifying numerically, and diagnosing failures in scientific Python data-analysis scripts.

## Key Capabilities

- **Pure-Stdlib Scientific Statistics & SVG Plotting (`scripts/stdlib_scientific_stats.py`)**: Runs natively on Python 3.12 standard library inside minimal offline containers (`python:3.12-slim`) with exact closed-form multivariable OLS ($\beta$, SE, HC3 robust SE, $t$, $p$, 95% CI, $R^2$, $R^2_{\text{adj}}$, $F$, Cohen's $f^2$, standardized $\beta$, VIF, Jarque-Bera, Breusch-Pagan, Durbin-Watson, Cook's $D$, `log1p` sensitivity), group inference (Brown-Forsythe/Levene, Classical ANOVA, Welch's heteroscedastic ANOVA, Kruskal-Wallis $H$, pairwise Welch $t$ + Holm-Bonferroni + Hedges' $g$, empty-group preservation), and publication-style pure-stdlib SVG figures.
- **Zero-Manual End-to-End Workflow (`scripts/auto_code_workflow.py`)**: Inspects `/input/task.md`, `/input/case.json`, and raw CSV/SQLite schemas to generate portable linear imperative analysis scripts (multivariable regression, group comparison, or read-only multi-table SQLite safe 1:N joins), executes all 7 verification gates, copies fresh deliverables to `/app/output`, and seals `result.json` with synchronized `uncertainty`/`gaps` and zero host paths.
- **Gate 7 Independent Numeric Cross-Check (`scripts/numeric_crosscheck.py`)**: Uses mathematically independent stdlib implementations (Welford's online algorithm for mean/SD, Householder QR decomposition for OLS $\beta$/$R^2$, direct SQLite cardinality queries) to verify `analysis_summary.json` within `<1e-9` relative error, catching silent "exit code 0 but wrong numbers" errors.
- **AST Static Code Contract Audit (`scripts/code_contract_checker.py`)**: Detects hardcoded host paths, silent zero-imputation (`fillna(0)`, `replace(NaN, 0)`, `nan_to_num`), unprotected CSV leading-zero ID ingestion, unseeded randomness, swallowed exceptions (`except: pass`), unsafe shell execution (`shell=True`), unintended SQLite writes, unchecked `groupby`/`.transform`/`.filter`/`.agg`/SQL `GROUP BY` aggregations, and missing inline checkpoints.
- **Offline Environment Lock (`scripts/env_lock_auditor.py`) & 7-Gate Isolated Runner (`scripts/isolated_code_runner.py`)**: Probes real Python/SQLite/package versions, enforces `PYTHONHASHSEED=42`, pre/post SHA-256 anti-stale guards, SQLite read-only immutability (`--db`, `--expected-query-rows`), and vector SVG gate summaries.
- **Harbor Core Suite (`M1-M6`) & Contract Sealer (`scripts/case_contract_sealer.py`)**: Includes `preflight_probe.py`, `result_guard.py`, `budget_guard.py`, `block_indexer.py`, `gap_gate.py`, and `final_audit.py`.

## Package Layout

```text
.
├── SKILL.md
├── README.md
├── references/
│   ├── code-contract-and-anti-silent-errors.md
│   ├── offline-env-lock-and-hash-audit.md
│   └── failure-triage-retry-and-isolation.md
├── scripts/
│   ├── auto_code_workflow.py
│   ├── stdlib_scientific_stats.py
│   ├── numeric_crosscheck.py
│   ├── isolated_code_runner.py
│   ├── code_contract_checker.py
│   ├── env_lock_auditor.py
│   ├── case_contract_sealer.py
│   ├── preflight_probe.py
│   ├── result_guard.py
│   ├── budget_guard.py
│   ├── block_indexer.py
│   ├── gap_gate.py
│   └── final_audit.py
└── examples/
    ├── example_analysis_script.py
    ├── example_workflow_trace.md
    └── example_output_manifest.json
```

## Quick Verification

```bash
python3 -B scripts/auto_code_workflow.py --input-dir /input --out-dir /app/output --stem exec_verification
python3 -B scripts/env_lock_auditor.py --script examples/example_analysis_script.py --out /tmp/env_lock.json
python3 -B scripts/code_contract_checker.py --script examples/example_analysis_script.py --out /tmp/audit.json
python3 -B scripts/isolated_code_runner.py --script examples/example_analysis_script.py --work-dir /tmp/work_demo --out-dir /tmp/out_demo --stem demo_exec
```

## Runtime & Compliance Notes

- Designed strictly for the **Scientific Python + SQLite** sandbox (no Office, PDF/LaTeX, OCR, or network dependency).
- All packaged resources are UTF-8 text files without symlinks, binary blobs, `.tsv` files, credentials, or host-specific paths.

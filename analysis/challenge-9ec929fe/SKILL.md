---
name: scientific-code-exec-verifier
description: >-
  Generate, environment-lock, execute in isolation, cross-verify numerically,
  and diagnose failures for reproducible scientific Python data-analysis scripts
  in an offline Scientific Python + SQLite sandbox. Provides pure-stdlib
  multivariable OLS (beta/SE/HC3/t/p/R2/F/VIF/JB/BP), group inference (Welch
  ANOVA, Kruskal-Wallis, Hedges' g, 95% CI), safe 1:N SQLite joins, publication
  SVG plots, AST static contract enforcement, SHA-256 anti-stale guards, and an
  independent Gate 7 numeric cross-check (<1e-9 relative error).
---

# Scientific Code Generation, Execution & Verification Workflow

## 1. Mission, Runtime Boundaries & Non-Goals

This skill governs end-to-end **scientific code generation, offline environment locking, isolated execution, independent numeric cross-verification, and honest failure triage** under the Harbor runtime contract:

- **Locations**: Skill root `/skills/weekly-skill` (`scripts/`, `references/`, `examples/`). Read actual `/input/case.json`, `/input/task.md`, and raw data snapshots (`.csv`, `.json`, `.sqlite`/`.db`) directly—never assume schemas from filenames. Stage work in `/app/work`; deliver verified outputs, figures, reports, and `/app/output/result.json` (preserving `case_id` and every `output_contract` field) in `/app/output`.
- **Environment**: **Offline Scientific Python + SQLite only** (Python 3.12 stdlib + SQLite3 guaranteed; third-party `numpy`/`pandas`/`scipy`/`matplotlib` probed dynamically, never assumed or installed). Every bundled script runs natively with zero third-party packages.
- **Budget**: 64 model requests, 900s wall-clock, 16,384 tokens/response. Use the zero-placeholder Fast-Path first, save verified results early, and cap repair loops.
- **Non-Goals**: Never fabricate data or library versions, never mutate `/input`, never substitute placeholder numbers after a crash, and never leak host paths.

## 2. Fast-Path Execution (Zero Placeholders, Recommended Default)

For standard `/input` CSV or SQLite cases, run the single-command schema-grounded workflow that parses `/input/case.json`, data dictionaries, `/input/task.md` semantic role clauses, and SQLite PRAGMAs (`table_info`, `foreign_key_list`, `index_list`), persists `analysis_plan.json` (stopping with `status="partial_with_gaps"` + structured `gaps` on any table/column/join-key/primary-key ambiguity—never guessing by row count or column order), generates a portable linear imperative analysis script with primary-key-bound `dedup_audit.json`, enforces all 7 verification gates, renders a publication-grade pure-stdlib SVG scientific figure, and seals `/app/output/result.json`:

```bash
SCRIPTS=/skills/weekly-skill/scripts

# 1. Discover /input layout and output_contract fields
python3 -B "$SCRIPTS/case_contract_sealer.py" discover --input-dir /input --out /app/work/input_manifest.json

# 2. Zero-manual end-to-end schema planning, 7-gate isolated execution, SVG plotting, and result sealing
python3 -B "$SCRIPTS/auto_code_workflow.py" \
  --input-dir /input \
  --out-dir /app/output \
  --stem exec_verification

# 3. Grounding and final deliverable audit
python3 -B "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B "$SCRIPTS/final_audit.py" --dir /app/output \
  --require analysis_plan.json \
  --require dedup_audit.json \
  --require exec_verification_report.json \
  --require exec_verification_report.md \
  --require exec_verification_gate_summary.svg \
  --require analysis_summary.json \
  --require scientific_figure.svg
```

### Deep-Audit Mode (Buggy Starter Scripts or Custom Queries)

When `/input` contains a defective starter script or custom multi-step SQLite requirements, pass `--script` or invoke the 7-gate tools individually:

```bash
SCRIPTS=/skills/weekly-skill/scripts
python3 -B "$SCRIPTS/env_lock_auditor.py" --script /app/work/analysis_task.py --out /app/work/env_lock.json --md-out /app/work/env_lock.md
python3 -B "$SCRIPTS/code_contract_checker.py" --script /app/work/analysis_task.py --input-csv /input/data.csv --strict --out /app/work/static_audit.json
python3 -B "$SCRIPTS/isolated_code_runner.py" \
  --script /app/work/analysis_task.py \
  --work-dir /app/work/exec_sandbox \
  --out-dir /app/output \
  --stem exec_verification \
  --seed 42 --timeout 120 \
  --input-csv /input/data.csv
```

For SQLite tasks, pass `--db /input/data.sqlite --expected-query-rows <N>` to `isolated_code_runner.py` to enforce read-only SHA-256 immutability, non-empty query cardinality, and Gate 7 SQLite cross-checks.

## 3. Core Scientific Code & Anti-Silent-Error Rules

1. **Linear Imperative Research Script Style**: Write scripts top-to-bottom (`config & seed -> load & schema check -> deduplication -> statistical model & diagnostics -> invariant checks -> export`) with inline checkpoints (`[checkpoint:load]`, `[checkpoint:dedup]`, `[checkpoint:ols]`, `[checkpoint:safe_join]`), explicit CLI path arguments (`--input`), and domain labels in outputs (never leaking internal flags).
2. **Preserve Leading-Zero Identifiers**: Load CSVs strictly as strings (`csv.DictReader` or `pd.read_csv(..., dtype=str)`) so codes like `0001` or `001` are never silently cast to integers.
3. **Separate True Zero from Missing (`NA`)**: Never call `.fillna(0)`, `.replace(NaN, 0)`, or `np.nan_to_num(..., nan=0)` unless explicitly authorized by the data dictionary (`# dictionary-authorized-zero`). Report `missing` and `true_zero_count` separately.
4. **Deduplication & Safe 1:N SQLite Joins**: Verify exact duplicates and primary keys before any `groupby`/`transform`/`filter`/`agg` or SQL `GROUP BY`. For 1:N child tables (e.g., replicate injections), pre-aggregate by sample key before `LEFT JOIN` to prevent row inflation, use dimension-table `LEFT JOIN` to preserve empty `n=0` groups, and never average coded integer columns (`*_code`, `*_id`).
5. **Deterministic Reproducibility**: Pin `PYTHONHASHSEED=42` and call `random.seed(42)` explicitly in every generated script so `explicit_seed_detected` and `dedup_check_detected` align strictly with `result.json`.
6. **Pure-Stdlib Scientific Modeling & Visualization (`stdlib_scientific_stats.py`)**:
   - **Multivariable OLS**: Normal equations + Gauss-Jordan partial-pivoting inversion yielding $\beta$, classical SE/$t$/$p$/95% CI, White-MacKinnon **HC3 robust SE**/$t$/$p$, $R^2$, $R^2_{\text{adj}}$, $F$, Cohen's $f^2$, standardized $\beta$, **VIF**, **Jarque-Bera** normality, **Breusch-Pagan** heteroscedasticity, Durbin-Watson, Cook's distance, and automatic `log1p` sensitivity for right-skewed predictors; renders 2-panel Observed-vs-Fitted + Residuals-vs-Fitted `scientific_figure.svg`.
   - **Group Comparison**: Per-group $n$/mean/SD/SE/95% CI/median/IQR (preserving empty `n=0` groups), Brown-Forsythe/Levene homogeneity test, Classical ANOVA ($\eta^2$, $\omega^2$), **Welch's heteroscedastic ANOVA**, tie-corrected **Kruskal-Wallis $H$** ($\varepsilon^2$), and pairwise Welch $t$-tests with Holm-Bonferroni adjustment + bias-corrected **Hedges' $g$**; renders jitter + mean$\pm$95% CI `scientific_figure.svg`.

## 4. Seven-Gate Verification & Failure Triage Matrix

`isolated_code_runner.py` enforces **7 mandatory gates** before marking `status="ok"`:

| Gate | Verifier | What It Blocks / Guarantees |
|---|---|---|
| 1. Static Contract | `code_contract_checker.py` | Blocks host paths, `fillna(0)`, leading-zero CSV corruption, unseeded RNG, swallowed `except: pass`, `shell=True`, SQLite writes, unchecked `groupby`/`.transform`/`.filter`/SQL `GROUP BY` |
| 2. Offline Env Lock | `env_lock_auditor.py` | Probes real Python/SQLite/package versions, computes SHA-256 env fingerprint, blocks missing offline imports prior to launch |
| 3. Isolated Execution | `isolated_code_runner.py` | Runs with `PYTHONHASHSEED=42`, single-threaded BLAS, `MPLBACKEND=Agg`, and bounded timeout |
| 4. Fresh Artifacts | Pre/post SHA-256 snapshot | Requires `fresh_artifacts >= min_artifacts` created/modified in the current run |
| 5. Structural & SQLite Invariants | JSON/CSV/SVG/SQLite checks | Blocks `NaN`/`Inf`, 0-byte/header-only CSVs, malformed SVGs, SQLite SHA-256 mutation, and query row-count mismatch (`--expected-query-rows`) |
| 6. Anti-Stale Guard | Marker lock | On any failure, deletes old `_VERIFIED.ok`, writes `_FAILED_MARKER.json`, sets `status="stale_or_failed"`, and lists `stale_preexisting_files` |
| 7. Independent Numeric Cross-Check | `numeric_crosscheck.py` | Independently recomputes raw/dedup row counts, Welford means/SDs, Householder-QR OLS $\beta$/$R^2$, group means/$\eta^2$, and SQLite join counts; requires `max_rel_err < 1e-9` |

### Bounded Failure Triage Rules

| Failure Class | Action | Retry Cap | Honest Stop Condition |
|---|---|---|---|
| `STATIC_CONTRACT_BLOCKER` | Fix AST blocker (remove host path/fillna(0)/unseeded RNG/unchecked groupby) | 2 | Contradictory data dictionary |
| `MISSING_OFFLINE_DEPENDENCY` | Switch to bundled `stdlib_scientific_stats.py` and log fallback in `uncertainty` | 1 | Specialized solver has no valid stdlib equivalent |
| `INPUT_SCHEMA_OR_ENCODING_ERROR` | Decode `utf-8-sig`, align exact column names, separate sentinel tokens | 2 | Required column genuinely absent from `/input` |
| `NUMERIC_CROSSCHECK_MISMATCH` / `INVARIANT_FAILURE` | Inspect dedup, join cardinality, NA filtering, or formula mismatch | 2 | Subgroup has 0 valid observations |
| `EXECUTION_TIMEOUT` / `SQLITE_PERMISSION_ERROR` | Remove $O(N^2)$ join or use `file:<db>?mode=ro` | 1 | Exceeds budget or requires mutating read-only input |

## 5. CLI Quick Reference & Output Contract

| Script | Primary Purpose | Key Flags | Exit `0` Condition |
|---|---|---|---|
| `auto_code_workflow.py` | Zero-manual `/input -> /app/output` workflow | `--input-dir`, `--out-dir`, `--stem`, `--script`, `--fast-path` | All 7 gates PASS + `result.json` sealed |
| `stdlib_scientific_stats.py` | Pure-stdlib OLS, group inference, SVG plots | Imported by generated/custom scripts | Zero third-party dependencies |
| `numeric_crosscheck.py` | Gate 7 independent Welford + QR + SQLite verifier | `--summary`, `--input-csv` / `--input-db`, `--tolerance 1e-9` | All checked metrics `rel_err < 1e-9` |
| `isolated_code_runner.py` | 7-gate isolated execution & anti-stale guard | `--script`, `--work-dir`, `--out-dir`, `--input-csv`, `--db`, `--expected-query-rows` | All 7 gates PASS |
| `code_contract_checker.py` | AST static anti-silent-error auditor | `--script`, `--input-csv`, `--strict`, `--out` | `blocker_count == 0` (and 0 warnings in `--strict`) |
| `env_lock_auditor.py` | Real offline runtime & import gap probe | `--script`, `--require`, `--out`, `--md-out` | `offline_dependency_gaps == []` |
| `case_contract_sealer.py` | `/input` discovery & `result.json` contract sealer | `discover` / `seal`, auto-syncs `uncertainty`/`gaps`, scrubs host paths | All `output_contract` fields verified |

**Sealed `/app/output/result.json` Contract**: Always preserve `case_id`, `status`, `results`, `metrics`, `provenance` (input/script SHA-256, env fingerprint, seed, relative artifact names), `uncertainty` (auto-populated with offline stdlib fallback, assumption violations such as JB/BP/Levene $p<0.05$, and preserved empty groups), and `gaps` (auto-populated with any missing dependencies, warnings, or invariant failures—never leave `gaps`/`uncertainty` blank when caveats exist).

## 6. Bundled References & Examples

- `references/code-contract-and-anti-silent-errors.md` — AST rules, anti-patterns, and linear script checklist.
- `references/offline-env-lock-and-hash-audit.md` — Environment manifest schema and SHA-256 snapshot protocol.
- `references/failure-triage-retry-and-isolation.md` — Failure taxonomy, retry limits, and anti-stale enforcement.
- `examples/example_analysis_script.py`, `examples/example_workflow_trace.md`, `examples/example_output_manifest.json` — Illustrative interface examples (not formal test outputs).

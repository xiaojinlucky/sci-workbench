---
name: scientific-code-exec-verifier
description: >-
  Generate, environment-lock, execute in isolation, verify numeric invariants,
  and diagnose failures for reproducible scientific Python data-analysis scripts
  inside a Scientific Python + SQLite sandbox. Enforces linear imperative
  research-script readability, AST static contract checks (blocking host paths,
  silent zero-imputation, unseeded RNG, leading-zero ID corruption, swallowed
  exceptions, and unintended SQLite writes), pre/post SHA-256 artifact hashing,
  anti-stale-artifact failure markers, and pure-stdlib SVG verification visuals.
---

# Scientific Code Generation, Execution & Verification Workflow

## 1. Mission, Runtime Boundaries & Scope

This skill governs end-to-end **scientific code generation, environment locking, isolated execution, numeric invariant verification, and honest failure triage** under the Harbor runtime contract:

- **Skill location**: `/skills/weekly-skill` (all scripts under `/skills/weekly-skill/scripts`, references under `/skills/weekly-skill/references`).
- **Case inputs**: `/input/case.json`, `/input/task.md`, plus raw data snapshots (`.csv`, `.json`, `.sqlite`/`.db`), data dictionaries, and optional starter or defective scripts. Always read the actual files under `/input` rather than inferring schemas from labels or filenames.
- **Working & output directories**: Perform intermediate generation, staging, and isolated runs under `/app/work`. Write final verified deliverables, reports, visuals, and `/app/output/result.json` (preserving all required `output_contract` fields and `case_id`) under `/app/output`.
- **Execution environment**: **Scientific Python + SQLite only** (standard library Python 3.12 + SQLite3 always available; optional scientific stack such as `numpy`, `pandas`, `scipy`, `matplotlib` probed dynamically at runtime). Do **not** assume Office, PDF/LaTeX renderers, OCR engines, or network access. All bundled verification scripts execute natively with zero third-party dependencies and enhance visuals with Matplotlib PNGs only when Matplotlib is genuinely installed.
- **Resource budget**: 64 model requests, 900 seconds wall-clock, 16,384 tokens per response including reasoning. Save honest partial outputs early and iterate incrementally.
- **Explicit non-goals**: Do not design wet-lab experiments, fabricate missing scientific data or software versions, overwrite original `/input` files, or hide runtime errors behind fallback placeholder numbers.

## 2. Core Research-Code & Anti-Silent-Error Principles

Scientific analysis code rarely fails cleanly; more often it exits `0` while returning corrupted science. Every generated or modified script must satisfy six non-negotiable engineering and scientific rules (detailed in `references/code-contract-and-anti-silent-errors.md`):

1. **Linear, Imperative Research-Script Clarity**:
   - Write analysis scripts top-to-bottom in a direct, inspectable sequence: *header context (purpose, inputs, outputs) -> configuration & seed -> load & schema check -> cleaning & deduplication -> statistical computation -> invariant checks -> artifact export*.
   - Keep parameters (thresholds, column names, seeds, grouping keys) right beside the step that uses them, with concise comments distinguishing dictionary rules from tool defaults.
   - Avoid unnecessary abstraction layers, deeply nested wrappers, or generic pipeline classes when a 40-120 line linear script makes every intermediate state directly readable and editable by a domain researcher.
   - Place inline checkpoints at key transitions (`len(rows)`, `df.shape`, `df.head(3)`, group counts / `Counter`, missing-value summary) so intermediate dimensions and category counts appear in execution logs.
   - Preserve raw tracebacks and fail fast on unexpected schema or type violations; never catch broad exceptions with `pass` or substitute guessed defaults.
   - **Separate scientific semantics from internal mechanics**: Output tables, report headings, and figure labels must use domain terms from the data dictionary (e.g., `alcohol_pct`, `cultivar_name`, `Not measured / NA`), never leaking internal pipeline flags (`is_valid_row`, `branch_A_fallback`).

2. **Preserve Leading-Zero Identifiers on Ingest**:
   - Sample codes (`0001`, `00123`, `001`) are text identifiers, never integers. When loading CSV via `pandas.read_csv`, explicitly pass `dtype={"sample_code": str, ...}` or `dtype=str` before casting numeric columns. In pure stdlib `csv.DictReader`, keep ID fields as strings and never pass them through `int()` / `float()`.

3. **Distinguish True Zero from Missing (`NA`) — Zero Silent Imputation**:
   - Never apply `.fillna(0)`, `.replace(np.nan, 0)`, or `np.nan_to_num(..., nan=0)` to scientific measurements unless the data dictionary explicitly authorizes zero imputation (and annotate the exact line with `# dictionary-authorized-zero` plus provenance).
   - Keep `missing_count`, `true_zero_count`, and `valid_n` separate in every summary table.

4. **Explicit Deduplication & Cardinality Verification Before Aggregation**:
   - Before any `GROUP BY`, mean, or sum, verify primary-key uniqueness and exact duplicate counts. Log dropped duplicates with their keys and counts; never silently aggregate duplicate instrument exports or 1-to-N joined rows.

5. **Deterministic Environment & Explicit Randomness Control**:
   - Probe and record exact Python, SQLite, OS, and installed scientific package versions via `env_lock_auditor.py`. Never assume a package exists without probing; never fabricate package versions.
   - Pin `PYTHONHASHSEED=42` and set explicit seeds (`random.seed(42)`, `np.random.default_rng(42)`, `random_state=42`) whenever any stochastic operation occurs.

6. **Anti-Stale-Artifact Discipline**:
   - When an execution run fails, times out, or violates a static/numeric invariant, the runner MUST flag `status="stale_or_failed"`, remove any prior verified-success stamp, write an explicit failure marker, and list any pre-existing unchanged files in the work directory so stale artifacts from an earlier run can never be mistaken for fresh valid results.

## 3. Standard Six-Stage Execution Pipeline

Execute every case strictly through the six stages below. Adapt paths only when `/input/case.json` specifies custom file locations.

```bash
SCRIPTS=/skills/weekly-skill/scripts

# Stage 0: Harbor preflight, budget ledger, result scaffold, and input indexing
python3 -B "$SCRIPTS/preflight_probe.py" --input /input --output /app/output --out /app/work/env_probe.json
python3 -B "$SCRIPTS/budget_guard.py" --state /app/work/.budget_state.json --init
python3 -B "$SCRIPTS/result_guard.py" --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "challenge-9ec929fe"
python3 -B "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# Stage 1: Real offline environment lock & dependency gap audit
python3 -B "$SCRIPTS/env_lock_auditor.py" \
  --out /app/work/env_lock_manifest.json \
  --md-out /app/work/env_lock_manifest.md

# Stage 2: Author or inspect the linear research script under /app/work/analysis_task.py
#          (Read /input data dictionary and sample rows first; preserve leading zeros & NA semantics)
python3 -B "$SCRIPTS/budget_guard.py" --state /app/work/.budget_state.json --tick "author_script"

# Stage 3: Static AST code-contract audit (with optional CSV header/ID cross-check)
python3 -B "$SCRIPTS/code_contract_checker.py" \
  --script /app/work/analysis_task.py \
  --input-csv /input/<PRIMARY_CSV_IF_ANY> \
  --out /app/work/code_contract_audit.json

# Re-check script imports against actual offline environment
python3 -B "$SCRIPTS/env_lock_auditor.py" \
  --script /app/work/analysis_task.py \
  --out /app/work/env_lock_manifest.json

# Stage 4: Isolated execution, pre/post SHA-256 snapshot, numeric invariants & SVG visual
python3 -B "$SCRIPTS/isolated_code_runner.py" \
  --script /app/work/analysis_task.py \
  --work-dir /app/work/exec_sandbox \
  --out-dir /app/output \
  --stem exec_verification \
  --seed 42 \
  --timeout 120

# Stage 5: Seal result.json with verified metrics/provenance and run grounding + final audit
python3 -B "$SCRIPTS/result_guard.py" \
  --path /app/output/result.json \
  --patch-json '{"artifacts":["exec_verification_report.json","exec_verification_report.md","exec_verification_gate_summary.svg"]}' \
  --finalize
python3 -B "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B "$SCRIPTS/final_audit.py" --dir /app/output \
  --require exec_verification_report.json \
  --require exec_verification_report.md \
  --require exec_verification_gate_summary.svg
```

### Stage-by-Stage Checkpoints & Stopping Rules

| Stage | Primary Tool | Required Evidence | Hard Stop / Escalation Condition |
|---|---|---|---|
| 0. Grounding | `preflight_probe.py`, `block_indexer.py` | Actual `/input` file list, data dictionary definitions, column types | Missing `/input/case.json` or unreadable source file -> record `BLOCKED` gap immediately |
| 1. Env Lock | `env_lock_auditor.py` | `env_lock_manifest.json` with exact Python/SQLite/package versions and `offline_dependency_gaps` | Script requires an uninstalled third-party package -> switch to stdlib/available library and document degradation |
| 2. Authoring | Linear research script | Readable script with inline checkpoints, explicit seeds, explicit `dtype` for IDs, read-only SQLite URI if querying DB | Ambiguous statistical definition not in dictionary -> document assumption in `uncertainty` |
| 3. Contract | `code_contract_checker.py` | `code_contract_audit.json` with `blockers == []` | Any `BLOCKER` (`CONTRACT_HOST_PATH`, `CONTRACT_SILENT_ZERO_IMPUTE`, `CONTRACT_SWALLOWED_EXCEPTION`, `CONTRACT_SHELL_INJECTION`, `CONTRACT_SQLITE_WRITE`, `CONTRACT_LEADING_ZERO_CSV`) blocks execution until fixed |
| 4. Isolation | `isolated_code_runner.py` | `exec_verification_report.json`, pre/post SHA-256 deltas, `fresh_artifacts >= 1`, `invariant_violations == []`, SVG summary | Non-zero exit, timeout, `NaN`/`Inf` leak, empty CSV, or row-count breach -> triggers `stale_or_failed` and bounded triage loop |
| 5. Sealing | `result_guard.py`, `gap_gate.py`, `final_audit.py` | Validated `/app/output/result.json` + zero ungrounded claims + all required artifacts present | `final_audit.py` fails -> repair missing artifact or downgrade `status` honestly to `partial`/`failed` |

## 4. Capability Discovery & Offline Degradation Policy

Before writing analysis code, inspect `env_lock_manifest.json` and classify each capability honestly using four states:
- `AVAILABLE`: Verified importable with recorded version (e.g., `sqlite3` stdlib, `csv`, `statistics`, `math`, or verified `numpy`/`pandas`/`scipy`/`matplotlib`).
- `UNAVAILABLE`: Confirmed absent in the offline container (`NOT_INSTALLED`); never attempt `pip install` or network download.
- `BLOCKED`: Tool exists, but required input file or schema column is missing from `/input`.
- `NOT_APPLICABLE`: Tool not required by the case (e.g., plotting library for a pure JSON/CSV verification task).

**Mandatory Stdlib-First Fallback**:
- If `pandas` or `numpy` is `UNAVAILABLE`, write the analysis script using Python 3.12 standard library (`csv`, `json`, `sqlite3`, `math`, `statistics`, `collections`) so the case still executes accurately and deterministically.
- If `matplotlib` is `UNAVAILABLE`, `isolated_code_runner.py` automatically produces a crisp, self-contained, editable vector SVG (`<stem>_gate_summary.svg`) using pure stdlib XML generation and records `"matplotlib_png_generated": false` honestly. When `matplotlib` IS installed, it renders both `<stem>_gate_summary.svg` and a 300-dpi `<stem>_gate_summary.png`.

## 5. Failure Triage, Bounded Retries & Anti-Stale Protocol

Never respond to an execution failure by blindly rewriting code or inventing plausible numbers. Follow `references/failure-triage-retry-and-isolation.md`:

| Failure Class | Typical Signal | Allowed Auto-Fix | Retry Cap | When to Stop & Report `partial`/`failed` |
|---|---|---|---|---|
| **Static Contract Blocker** | `CONTRACT_*` blocker in `code_contract_audit.json` | Remove host path, replace `fillna(0)` with explicit missing handling, add `dtype=str` for leading-zero IDs, pin RNG seed, remove bare `except: pass` | 2 rounds | Stop if data dictionary itself is contradictory about zero vs missing |
| **Missing Offline Package** | `ModuleNotFoundError` or `offline_dependency_gaps` non-empty | Rewrite using verified available stdlib/scientific packages; record degradation note | 1 round | Stop if algorithm requires an unavailable specialized solver with no valid substitute |
| **Schema / Type Mismatch** | `KeyError`, `ValueError` on parse, leading-zero mismatch, BOM header `\ufeff` | Strip UTF-8 BOM (`utf-8-sig`), align exact column name from data dictionary, separate non-numeric tokens (`NA`, `<LOD`) | 2 rounds | Stop if required column genuinely does not exist in `/input` |
| **Numeric Invariant Violation** | `NaN`/`Inf` in JSON/CSV, 0-byte output, header-only CSV, row inflation | Inspect deduplication/join/division-by-zero logic; guard empty denominators explicitly as `null` with reason | 2 rounds | Stop if source data has 0 valid non-null observations for that subgroup |
| **Timeout / Resource Limit** | `status="timeout"` in runner report | Profile bottleneck, remove accidental $O(N^2)$ loop or cartesian join, use streaming/indexed lookup | 1 round | Stop if dataset genuinely exceeds container budget; save partial checkpoint |
| **SQLite Permission / Write Attempt** | `sqlite3.OperationalError: attempt to write a readonly database` | Remove write/DDL statement; query via `file:<path>?mode=ro` and compute derived outputs in `/app/work` | 1 round | Stop if task explicitly requires mutating a read-only input |

**Hard Anti-Stale Rule**: Every failed run in `isolated_code_runner.py` writes `<stem>_FAILED_MARKER.json` and sets `"status": "stale_or_failed"` in `<stem>_report.json`. Any files that existed in `--work-dir` before the failed run are explicitly listed under `stale_preexisting_files` and excluded from `fresh_artifacts`. Never copy stale files from a previous run into `/app/output`.

## 6. Required Output Contract & Verification Evidence

Before finishing, confirm `/app/output/result.json` contains:
- `case_id` matching `/input/case.json` and all original `output_contract` fields required by `task.md`.
- `status`: `"ok"` (all checks pass), `"partial"` (verified subset delivered with explicit `gaps`), or `"failed"` (honest diagnostic report, zero fabricated numbers).
- `metrics` and `metric_provenance`: every reported metric paired with its exact source file, script line/function, and calculation rule.
- `sources` / `environment`: exact input file SHA-256 hashes, script SHA-256 hash, Python/SQLite/package versions, and `PYTHONHASHSEED`.
- `artifacts`: relative filenames under `/app/output` (`exec_verification_report.json`, `exec_verification_report.md`, `exec_verification_gate_summary.svg`, plus any case-required tables/charts/scripts).
- `uncertainty` and `change_records`: explicit notes on missing values, excluded outliers/duplicates, offline fallbacks, and any retry repairs performed.

### CLI Quick Reference for Bundled Tools

| Script | Required Flags | Optional Flags | Exit Code Semantics |
|---|---|---|---|
| `env_lock_auditor.py` | *(none)* | `--script <py>`, `--require <pkg1,pkg2>`, `--out <json>`, `--md-out <md>` | `0` if zero offline dependency gaps; `1` if script/required packages are missing |
| `code_contract_checker.py` | `--script <py>` | `--input-csv <csv>`, `--out <json>`, `--strict` | `0` if `blocker_count == 0` (and `warning_count == 0` under `--strict`); `1` otherwise |
| `isolated_code_runner.py` | `--script <py>`, `--work-dir <dir>`, `--out-dir <dir>` | `--stem <name>`, `--seed <int>`, `--timeout <s>`, `--input-csv <csv>`, `--expected-rows <n>`, `--min-artifacts <n>`, `--strict-static`, `-- <script_args>` | `0` only if static + env + exit=0 + fresh artifacts + invariants all pass; `1` on `stale_or_failed` or `timeout` |
| `case_contract_sealer.py` | `discover` or `seal` | `--input-dir`, `--case-json`, `--result-json`, `--task-id`, `--payload-json`, `--artifacts` | `0` when `/input` discovered or all `output_contract` fields sealed into `result.json` |

### 64-Request Budget Fast-Path vs Deep-Audit Mode

- **Fast-Path (default for clean single-table/SQLite tasks, saves request budget)**: Run `case_contract_sealer.py discover` -> `isolated_code_runner.py` (which internally invokes static AST checking + SHA-256 snapshotting + numeric invariant scanning in one pass) -> `case_contract_sealer.py seal` -> `final_audit.py`. Skip redundant standalone `env_lock_auditor.py` / `code_contract_checker.py` re-runs unless an import or static blocker fires.
- **Deep-Audit Path (use when debugging buggy starter scripts or strict multi-dependency environments)**: Run standalone `env_lock_auditor.py --script ...` and `code_contract_checker.py --strict` before entering `isolated_code_runner.py`, capping retries at 2 targeted repairs per failure mode.

### Canonical Anti-Pattern vs. Safe Research-Code Comparison

```python
# UNSAFE (blocked by code_contract_checker.py):
df = pd.read_csv("/home/researcher/wine.csv")  # host path + drops leading zeros on sample_code
df["proline"] = df["proline"].fillna(0)        # silently converts missing NA into numeric 0.0
summary = df.groupby("cultivar_name")["proline"].mean()  # no dedup check, no seed, no checkpoints

# SAFE (passes static audit + reproducible in offline Python 3.12 stdlib):
SEED = 42  # explicit seed adjacent to analysis parameters
with input_csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
    raw_rows = [r for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())]
print(f"[checkpoint:load] rows={len(raw_rows)} sample_head={raw_rows[:2]}")
# Deduplicate by text sample_code (preserving '0001'), keep NA separate from true 0.0
```

### Pre-Submission Quality & Honesty Checklist

1. Every numeric metric reported in `result.json` traces directly to a **fresh** artifact verified in `exec_verification_report.json` (never to `stale_preexisting_files`).
2. `code_contract_audit.json` shows `blocker_count == 0` and no unreviewed `CONTRACT_SILENT_ZERO_IMPUTE` or `CONTRACT_LEADING_ZERO_CSV` violations.
3. `env_lock_manifest.json` records real probed Python/SQLite/library versions without claiming uninstalled packages exist.
4. `exec_verification_gate_summary.svg` exists under `/app/output`, is valid UTF-8 XML, and matches the reported gate status.
5. `gap_gate.py` and `final_audit.py` both exit `0`, and `budget_guard.py` confirms completion within the 64-request / 900-second ceiling.

## 7. Reference Library & Examples

Read these bundled references whenever hitting edge cases:
- `references/code-contract-and-anti-silent-errors.md` — Detailed AST rules, anti-patterns, linear research-script checklist, and data-dictionary alignment rules.
- `references/offline-env-lock-and-hash-audit.md` — Deterministic environment manifest format, offline dependency resolution, and pre/post SHA-256 snapshot auditing.
- `references/failure-triage-retry-and-isolation.md` — Structured error taxonomy, retry caps, isolation sandbox rules, and anti-stale-artifact enforcement.
- `examples/example_analysis_script.py` — Runnable stdlib-only linear research script demonstrating leading-zero preservation, NA vs zero separation, and inline checkpoints.
- `examples/example_workflow_trace.md` and `examples/example_output_manifest.json` — Illustrative end-to-end invocation and output schema (labeled as example only).

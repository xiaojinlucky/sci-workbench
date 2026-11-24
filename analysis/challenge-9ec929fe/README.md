# Scientific Code Generation, Execution & Verification Skill (`challenge-9ec929fe`)

A self-contained, offline-capable Harbor Skill for generating, environment-locking, executing in isolation, verifying numeric invariants, and diagnosing failures in scientific Python data-analysis scripts.

## Key Capabilities

- **Zero-Dependency Native Core**: Every bundled audit and verification script runs natively on Python 3.12 standard library (`ast`, `sqlite3`, `csv`, `json`, `hashlib`, `subprocess`, `statistics`) inside minimal offline containers (`python:3.12-slim`), with automatic optional Matplotlib PNG rendering when Matplotlib is installed.
- **AST Static Code Contract Audit (`scripts/code_contract_checker.py`)**: Detects hardcoded host paths, silent zero-imputation (`fillna(0)`, `replace(NaN, 0)`, `nan_to_num`), unprotected CSV leading-zero ID ingestion, unseeded randomness, swallowed exceptions (`except: pass`), unsafe shell execution (`shell=True`), unintended SQLite writes, unchecked `groupby` aggregation, and missing inline verification checkpoints.
- **Offline Environment Lock Auditor (`scripts/env_lock_auditor.py`)**: Probes actual Python, SQLite, OS, and scientific package versions without fabricating missing libraries, cross-checks target script AST imports against the container environment, and emits `env_lock_manifest.json` with a deterministic fingerprint.
- **Isolated Runner & Anti-Stale Guard (`scripts/isolated_code_runner.py`)**: Runs scripts in a controlled working directory with `PYTHONHASHSEED=42`, timeout protection, pre/post SHA-256 directory snapshots, JSON/CSV numeric invariant verification (NaN/Inf, empty files, row-count conservation), explicit `stale_or_failed` markers on failure, and pure-stdlib vector SVG gate summaries.
- **Harbor Core Suite (`M1-M6`)**: Includes `preflight_probe.py`, `result_guard.py`, `budget_guard.py`, `block_indexer.py`, `gap_gate.py`, and `final_audit.py`.

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
│   ├── preflight_probe.py
│   ├── result_guard.py
│   ├── budget_guard.py
│   ├── block_indexer.py
│   ├── gap_gate.py
│   ├── final_audit.py
│   ├── code_contract_checker.py
│   ├── env_lock_auditor.py
│   └── isolated_code_runner.py
└── examples/
    ├── example_analysis_script.py
    ├── example_workflow_trace.md
    └── example_output_manifest.json
```

## Quick Verification

```bash
python3 -B scripts/env_lock_auditor.py --script examples/example_analysis_script.py --out /tmp/env_lock.json
python3 -B scripts/code_contract_checker.py --script examples/example_analysis_script.py --out /tmp/audit.json
python3 -B scripts/isolated_code_runner.py --script examples/example_analysis_script.py --work-dir /tmp/work_demo --out-dir /tmp/out_demo --stem demo_exec
```

## Runtime & Compliance Notes

- Designed strictly for the **Scientific Python + SQLite** sandbox (no Office, PDF/LaTeX, OCR, or network dependency).
- All packaged resources are UTF-8 text files without symlinks, binary blobs, `.tsv` files, credentials, or host-specific paths.

# Paper Reproduction & Method Verification Skill (`challenge-da62e8d4`)

Offline-first, zero-mandatory-dependency Skill for environment/lockfile drift auditing, deep AST hyperparameter & seed hygiene reconciliation, isolated multi-run execution with jitter detection, a priori tolerance verification, and evidence-backed 5-axis discrepancy attribution in Harbor containers.

## Runtime & Dependencies
- **Required**: Python 3.12 standard library only (`ast`, `tomllib`, `importlib.metadata`, `subprocess`, `hashlib`, `json`; verified offline in `python:3.12-slim` with `--network none`).
- **No external network or third-party installation required**.

## Quickstart
```bash
python -B scripts/env_lock_doctor.py --input /input --out /app/work/env_doctor.json
python -B scripts/repro_audit_runner.py --input /input --output /app/output --work /app/work --repeats 2
python -B scripts/final_audit.py --dir /app/output --result-file result.json --require reproduction_audit_report.json --require reproduction_report.md
```

## Directory Layout
- `SKILL.md` — DeepSeek Harness executable workflow, 4-tier evidence ladder, and hard boundaries.
- `scripts/env_lock_doctor.py` — Conda/Pip/UV/pyproject/Dockerfile manifest scanner, Conda #15581 mixed-source drift detector, runtime version comparator, and `research-repo-doctor` checklist generator.
- `scripts/repro_audit_runner.py` — Deep AST parameter/seed auditor, isolated multi-run executor with `PYTHONHASHSEED` and jitter detection, failed-run segregating tolerance verifier, 5-axis attribution engine, and Markdown report generator.
- `scripts/audit_chain.py` — SHA-256 hash-chained audit log.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py` — Harbor runtime guardrails (M1–M6).
- `references/` — Detailed protocols for `research-repo-doctor` checklists, lockfile/seed auditing, 5-axis discrepancy attribution, and missing-data boundaries.
- `examples/` — Minimal synthetic input case, candidate script, and schema example (illustrative only).

## Scope & Limits
- Audits supplied candidate scripts, manifests, and declared tolerances only; synthetic trials do not replace unavailable author repositories, physical experiments, or full external PMLB datasets.
- Failed runs are strictly excluded from tolerance statistics and reported separately.
- Does not install missing packages over the network; flags declared-vs-installed runtime gaps honestly.

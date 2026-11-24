# Illustrative Workflow Trace (Example Only)

> Note: All outputs shown below are **illustrative examples** demonstrating the Skill's CLI contract and output structure; they are NOT formal benchmark answers.

## 1. Environment Lock Probe

```bash
python3 -B /skills/weekly-skill/scripts/env_lock_auditor.py \
  --script /skills/weekly-skill/examples/example_analysis_script.py \
  --out /app/work/env_lock_manifest.json
```

Expected behavior:
- Reports actual `python_version`, `sqlite_version`, OS, and whether each scientific library (`numpy`, `pandas`, `scipy`, `matplotlib`, etc.) is `AVAILABLE` or `NOT_INSTALLED`.
- Verifies all imports in `example_analysis_script.py` belong to Python standard library (`offline_dependency_gaps == []`).

## 2. Static AST Contract Audit

```bash
python3 -B /skills/weekly-skill/scripts/code_contract_checker.py \
  --script /skills/weekly-skill/examples/example_analysis_script.py \
  --out /app/work/code_contract_audit.json
```

Expected behavior:
- Returns `ok: true`, `blocker_count: 0`, `warning_count: 0`.
- Confirms inline verification checkpoints, explicit RNG seed, no hardcoded host paths, no silent `.fillna(0)`, and no swallowed exceptions.

## 3. Isolated Execution, Snapshot Hashing & Visual Gate Summary

```bash
python3 -B /skills/weekly-skill/scripts/isolated_code_runner.py \
  --script /skills/weekly-skill/examples/example_analysis_script.py \
  --work-dir /app/work/exec_box \
  --out-dir /app/output \
  --stem exec_verification \
  --seed 42 \
  --timeout 120
```

Expected outputs in `/app/output/`:
- `exec_verification_report.json` — full audit payload including pre/post SHA-256 snapshot diff, `fresh_artifacts`, `invariant_violations`, and stdout/stderr tails.
- `exec_verification_report.md` — concise human-readable verification summary.
- `exec_verification_gate_summary.svg` — editable pure-stdlib SVG showing all 6 execution gates.
- `exec_verification_VERIFIED.ok` — integrity stamp (automatically replaced by `exec_verification_FAILED_MARKER.json` if any rerun fails).

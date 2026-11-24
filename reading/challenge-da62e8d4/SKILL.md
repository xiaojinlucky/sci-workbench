---
name: reproduction-audit-verifier
description: Audit environment manifests and Conda/Pip/UV lockfile drift (Conda #15581/PR #15838), generate a research-repo-doctor executable checklist, reconcile paper hyperparameters via deep AST inspection (constants, function/argparse/dataclass/dict defaults and RNG seed hygiene), run candidate numerical methods across isolated repeats with PYTHONHASHSEED and SHA-256 provenance, verify a priori tolerances with failed-run exclusion and evidence-backed 5-axis attribution, and distinguish the 4-tier evidence ladder while preserving missing original data gaps.
---

# Paper Reproduction & Method Verification (`challenge-da62e8d4`)

## Purpose & Trigger Scope
Use this Skill for paper reproduction and computational method verification tasks where candidate numerical implementations must be audited against paper specifications, runtime manifests, and a priori tolerances.

This Skill integrates four operational disciplines into a deterministic, offline-first **pure Python 3.12 standard library** workflow (`python:3.12-slim`, zero mandatory third-party packages):
1. **Runtime Verification Contract**: define expected targets independently, execute in clean isolated subdirectories, record exact commands/hashes/exit codes, and treat unresolved datasets or failed runs as unvalidated rather than silent passes.
2. **Research-Repo-Doctor Executable Checklist**: separate `auto_discovered` repository facts, `manual_action_items`, and `missing_items` without confusing engineering readiness with scientific reproduction.
3. **Conda #15581 / PR #15838 Lockfile Drift Audit**: flag mixed Conda+pip manifests without strict lockfiles, unpinned specs (`>=`, `*`, bare names), platform-bound build hashes, and declared-vs-installed runtime version drifts.
4. **In-Place Verification & No-Swallowed-Errors Discipline**: retain raw exit codes, stdout/stderr tails, stochastic jitter across repeated runs, and separate backend execution state from scientific reproducibility claims.

## Four-Tier Evidence Ladder (Mandatory Distinction)
Never collapse these four tiers into a single "reproduced" claim:
- **Tier 1 (`code_present`)**: candidate numerical scripts exist in `/input` and parse cleanly.
- **Tier 2 (`clean_exit`)**: every candidate run exits with return code `0` in an isolated working directory without timeout or runtime errors.
- **Tier 3 (`within_prior_tolerance`)**: clean-exit numeric metrics fall inside pre-declared `abs_tol`/`rel_tol` AND remain deterministic across isolated repeat runs (`max_run_jitter == 0`).
- **Tier 4 (`scientific_claim_supported`)**: requires original author code, raw data/splits (e.g., full PMLB or physical experiments), and full verification; whenever `missing_original_materials` is non-empty or only a synthetic trial is supplied, Tier 4 MUST remain `"not_established"`.

## Runtime Paths
- **Input root**: `/input` (`case.json`, `task.md`, candidate `.py` scripts, optional `environment.yml`, `requirements.txt`, `pyproject.toml`, `conda-lock.yml`, `uv.lock`, `Dockerfile`, `README.md`, paper excerpts).
- **Work directory**: `/app/work` (`env.json`, `.budget_state.json`, `env_doctor.json`, `audit_log.json`, isolated `run_<script>_r1/r2/` subdirectories, `gaps.json`).
- **Output directory**: `/app/output` (`result.json`, `reproduction_audit_report.json`, `reproduction_report.md`).

## Deterministic Execution Workflow
Locate scripts relative to this `SKILL.md` directory (`scripts/`).

### Step 1: Preflight, Lockfile Drift Doctor & Controlled Multi-Run Audit
```bash
python -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python -B scripts/env_lock_doctor.py --input /input --out /app/work/env_doctor.json
python -B scripts/repro_audit_runner.py --input /input --output /app/output --work /app/work --repeats 2
```
`repro_audit_runner.py` executes the full deterministic audit:
1. **Environment & Lockfile Drift (`env_lock_doctor.py`)**: scans `environment.yml`, `requirements*.txt`, `pyproject.toml`, `conda-lock.yml`, `uv.lock`, `poetry.lock`, `Dockerfile`, `README*`, `LICENSE*`, `CITATION.cff`; flags Conda+pip mixing, unpinned deps, platform-bound builds, and declared-vs-installed runtime version drift; emits the `research_repo_doctor_checklist`.
2. **Deep AST Hyperparameter & Seed Hygiene Audit**: extracts top-level constants (including negative & annotated), function defaults, `argparse.add_argument(..., default=...)`, config dicts, and `@dataclass` field defaults; inspects `random.seed`, `np.random.seed`, `default_rng`, `torch.manual_seed`, `PYTHONHASHSEED`, and unseeded stochastic RNG calls; classifies every parameter into `matched`, `paper_code_conflict`, `paper_only_missing_in_code`, or `code_only_undocumented`.
3. **Isolated Repeat Execution & Jitter Check**: executes every candidate script across isolated subdirectories (`--repeats 2` by default) with `PYTHONHASHSEED` injected, recording exact commands, exit codes, elapsed times, and input/output SHA-256 digests. Compares repeat outputs to detect stochastic jitter (`STOCHASTIC_RUN_JITTER`).
4. **Failed-Run Segregation & A Priori Tolerance Verification**: excludes failed/timeout runs from tolerance pass/fail counts and lists them separately in `failed_runs_excluded[]`. Evaluates clean-exit metrics strictly against pre-declared `abs_tol`/`rel_tol` without post-hoc relaxation.
5. **Evidence-Backed Five-Axis Discrepancy Attribution**: assigns primary and supporting root-cause axes (`environment_or_seed`, `parameter_mismatch`, `data_or_split_difference`, `implementation_difference`, `paper_underspecification`) with concrete line/jitter/drift/missing-data evidence.
6. **Report Generation**: writes `/app/output/reproduction_audit_report.json`, `/app/output/reproduction_report.md`, and `/app/output/result.json`.

### Step 2: Host Agent Scientific Synthesis & Evidence Cross-Check
Inspect `/app/output/reproduction_audit_report.json` and `/app/output/reproduction_report.md` alongside the paper prose in `/input`:
1. Confirm that every paper-reported hyperparameter, split ratio, and preprocessing step from the paper text/appendix is reflected in the reconciliation table (flagging unstated details as `paper_underspecification` rather than guessing).
2. Verify that failed runs and omitted metrics are reported separately from successful tolerance counts.
3. Verify that `Data/Code Availability` and missing original repositories or datasets (such as unavailable PMLB splits or proprietary checkpoints) remain explicitly documented in `missing_original_materials[]`.

### Step 3: Grounding & Final Deliverable Audit
```bash
python -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python -B scripts/final_audit.py --dir /app/output --result-file result.json --require reproduction_audit_report.json --require reproduction_report.md
```

## Reference Specifications
- `references/research_repo_doctor_checklist.md` — Executable repository checklist structure (`auto_discovered`, `manual_action_items`, `missing_items`).
- `references/env_lockfile_drift_and_seed_audit.md` — Conda #15581/PR #15838 drift detection, unpinned/platform-bound diagnostics, and RNG seed hygiene rules.
- `references/five_axis_attribution_and_tolerance_protocol.md` — Four-tier evidence ladder, a priori tolerance discipline, failed-run exclusion, and 5-axis attribution mapping.
- `references/output_contract_and_missing_data_boundary.md` — Deliverable schema, required fields, and strict boundaries on synthetic trials vs original research reproduction.

## Hard Boundaries & Failure Handling
- **Never Relax Tolerances Post-Hoc**: `abs_tol` and `rel_tol` come strictly from pre-declared inputs.
- **Never Fabricate Missing Data or Baselines**: retain missing raw datasets, unavailable author repositories, and unreported paper metrics explicitly as missing.
- **Segregate Failed Executions**: non-zero exit codes or timeouts are excluded from tolerance denominators and listed in `failed_runs_excluded[]`.
- **No Unfounded "Reproduced" Claims**: "code exists" (`Tier 1`) or "exits 0" (`Tier 2`) does not establish `Tier 3` (within tolerance & deterministic) or `Tier 4` (scientific claim supported).

# Research Methods & Statistical Audit Skill (`challenge-9e7302fd`)

Offline-first, zero-third-party-dependency research methods and statistical review Skill engineered for an offline Python 3.12 sandbox (`python:3.12-slim --network none`).

## Core Capabilities

1. **Pure-Stdlib High-Precision StatCheck (`stat_math_core.py`)**: Exact two-sided and one-sided p-value recomputation for `t`, `F`, `chi2`, `r`, and `z` via Modified Lentz continued fractions for regularized incomplete Beta/Gamma (`< 1e-9` accuracy). Automatically cross-checks against `scipy.stats` when installed and never crashes when absent.
2. **Strict Inequality Boundaries & Reporting Hygiene**: Enforces strict `p < x`, inclusive `p <= x`, and strict `p > x` without lax tolerances; accounts for test-statistic rounding intervals on `p = x`; detects invalid `p = 0.000` and vague spin phrasing.
3. **Effect-Size & 95% CI vs p-Value Consistency**: Parses `OR/HR/RR/IRR` (null = `1.0`) and `beta/MD/SMD/Cohen's d/Hedges' g` (null = `0.0`) with 95% CIs and p-values to catch inverted CIs, point estimates outside CIs, and CI-vs-p contradictions—while respecting multiplicity-adjusted p-values.
4. **GRIM, Integer Proportions, Group-N Conservation & Pseudoreplication**: Verifies `k/n (pct%)`, discrete-scale GRIM granularity (gated to discrete/Likert scales), `sum(group_n)` vs total `N`, and CNS-level experimental-unit mismatches (e.g., `N = 6 mice` with `t(598)` across cells without mixed-effects nesting).
5. **Five-Facet Methodology Coverage & Strict Three-Way Separation**: Audits Study Design, Variable Definitions, Statistical Methods, Sample Size/Power, and Results Reporting, strictly separating `decision_error`, `method_flaw`, and `under_reported` (listing `missing_inputs` and `unassessable_items` instead of accusing authors of p-hacking).

## Package Layout

- `SKILL.md`: DeepSeek Harness execution protocol and CNS/ACL calibration rules.
- `scripts/stat_math_core.py`: Pure-stdlib exact statistical distributions (`< 1e-9` accuracy, self-testable).
- `scripts/statcheck_and_phack_auditor.py`: Main deterministic audit engine producing `stats_audit.json`, `stats_review_report.md`, and dual-compatible `result.json` (via `--out-result-json`), including basic `.csv` table reconciliation.
- `scripts/phack_detect.py`: Pure-stdlib desensitized p-curve diagnostic module (runs only when `n_significant >= 5`).
- `scripts/review_io_core.py`: Pure-stdlib document parser (MD/TXT/TEX/DOCX/PDF cascade + wrapped-paragraph merging + section splitter).
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: Offline sandbox execution, grounding, and contract verification utilities.
- `references/{methods_stats_rubric,evidence_and_fairness_protocol,phack_concepts,output_contract_spec}.md`: Domain rubrics and contract specs.
- `examples/minimal_example.md`: Minimal reproducible CLI usage example with labeled illustrative output.

## Quick Start (Offline Python 3.10+)

```bash
python -B scripts/stat_math_core.py
python -B scripts/statcheck_and_phack_auditor.py \
  --input /input \
  --out-json /app/output/stats_audit.json \
  --out-md /app/output/stats_review_report.md \
  --out-result-json /app/output/result.json
```

## Known Scope & Limitations

- Raw-data assumptions (normality, homoscedasticity, exact model specification) cannot be verified when raw tabular datasets are not supplied; these boundaries are explicitly recorded in `unassessable_items`.
- Single-manuscript p-curve statistics are purely descriptive and never establish intentional p-hacking.

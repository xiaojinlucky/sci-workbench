# Statistical Inference, Effect Size & Interpretation Skill (`challenge-c0e1e220`)

A Dual-Backend (pure Python 3.12 standard library + optional SciPy/Matplotlib enhancement) Skill for rigorous scientific statistical analysis and restrained interpretation inside offline Scientific Python + SQLite environments.

## Structure

- `SKILL.md`: Complete executable statistical workflow, routing tree, reporting rules, and Harbor contract.
- `references/`:
  - `statistical_decision_tree_and_small_sample.md`: Decision rules, Welch's ANOVA & Welch-Satterthwaite formulas, and small-sample/zero-variance degradation.
  - `effect_size_and_ci_matrix.md`: Effect size & 95% CI bindings across parametric, non-parametric, paired, correlation, and contingency designs.
  - `misuse_checklist_and_reporting_spec.md`: Ten anti-misuse guardrails and front-end scientific expression standards.
- `scripts/`:
  - Core Harbor helpers (`preflight_probe.py`, `result_guard.py`, `budget_guard.py`, `block_indexer.py`, `gap_gate.py`, `final_audit.py`).
  - `assumption_diagnostics.py`: Data hygiene, encoded-integer column blocker, outlier audit, normality, and Brown-Forsythe homogeneity diagnostics.
  - `inference_effect_ci_runner.py`: Design-matched inference, effect sizes, 95% CIs, Holm/BH corrections, linear `reproduce_stats.py` generator, and vector SVG (+ optional Matplotlib PNG/PDF/SVG) exporter.
- `examples/`: Minimal CSV/JSON invocation examples (zero `.tsv`, zero precomputed formal case answers).

## Quick Verification

```bash
python3 scripts/assumption_diagnostics.py --input examples/minimal_sample.csv --group-col group --value-col response_mg_l
python3 scripts/inference_effect_ci_runner.py --input examples/minimal_sample.csv --group-col group --value-col response_mg_l --unit "mg/L" --out-dir /tmp/stats_demo --stem demo
```

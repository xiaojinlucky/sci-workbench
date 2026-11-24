# Distributional p-Curve Diagnostics: Concepts & Epistemic Boundaries

This reference explains when and how `scripts/phack_detect.py` may be interpreted during statistical review.

## 1. Core Statistical Principles

Under an unmanipulated literature with genuine effects, the conditional distribution of significant p-values on `(0, alpha)` is right-skewed and non-increasing (concave CDF). The pure-stdlib diagnostics in `phack_detect.py` evaluate:

- **Binomial half-interval test**: Share of significant p-values falling below `alpha / 2` (null >= 0.5).
- **Fisher & Stouffer pp-value aggregation**: Tests right-skew (evidential value) vs left-skew (excess mass near `alpha`).
- **Least Concave Majorant (LCM) monotonicity test** (Elliott, Kudrin & Wuethrich, 2022): Measures departure of the empirical CDF on `(0, alpha)` from concavity against a uniform null via deterministic seeded bootstrap.
- **Smooth counterfactual discontinuity & Gerber-Malhotra caliper**: Compares observed mass just past the significance threshold against a Poisson log-linear counterfactual fitted outside a donut window.
- **Simonsohn p-curve power estimate**: Estimates implied average statistical power from the distribution of significant p-values.

## 2. Hard Guardrails for Manuscript Review

1. **Minimum Sample Gate (`n_significant >= 5`)**: When a manuscript contains fewer than 5 exact significant `p = ...` values, the battery MUST remain skipped (`ran: false`) to prevent underpowered false positives.
2. **Cross-Study Method, Not Single-Paper Conviction**: Every test in `phack_detect.py` assumes independent test statistics drawn across studies. Correlated tests within a single paper naturally violate independence. Therefore, **a distributional signal NEVER proves p-hacking or misconduct**—it only prompts inspection of pre-registration, multiplicity reporting, and primary-vs-secondary endpoint separation.
3. **Zero-Dependency Execution**: All distributions and linear solvers run in pure Python via `stat_math_core.py`, ensuring full reproducibility in offline Linux containers without `scipy` or `numpy`.

# Interaction Reversal, Search Strategy Selection, and Cost Trade-Off Rules

## 1. Why OFAT Fails Under Factor Interaction

In One-Factor-At-a-Time (OFAT) tuning, each parameter is set to its marginal mean optimum independently. When a 2-way interaction `x1 * x2` is present:
- The slope of `x1` can **reverse sign** between low `x2` and high `x2`.
- Concatenating marginal optima `(x1_marginal_best, x2_marginal_best)` can land on a strictly suboptimal or even unsafe corner.
- Always run `interaction_and_surrogate_analyzer.py` on pilot/historical runs to inspect `empirical_sign_reversals`, `r2_lift_from_interactions`, and `ofat_strictly_suboptimal`.
- When `interaction_reversal_detected: true`, propose joint factorial, central-composite (RSM), or uncertainty-guided sequential confirmatory runs inside the verified feasible domain.

## 2. Search Strategy Decision Matrix

| Scenario | Recommended Strategy | Stopping Criterion Pattern |
| --- | --- | --- |
| <= 25 feasible discrete points, low run cost | `grid` | Exhaustive evaluation of feasible points + 2x replicate confirmation |
| >= 4 factors, tight run budget | `fractional_factorial` or `screening_then_confirm` | Resolution >= IV screening complete; advance top 2–3 active factors |
| 2–4 continuous factors with curvature/interaction | `rsm_ccd` or `sequential_doe` | Center-point lack-of-fit p > 0.05 and predicted gain < 2*measurement_SD |
| Expensive runs (> 2 h or high reagent cost) | `bayesian_bo` | Expected improvement < noise floor or budget/machine-hour cap reached |

## 3. Pre-mortem Risk & Confirmation Gate

Every optimization package must separate **surrogate prediction** from **measured confirmation**:
1. Mark all previously untested cells (`previously_untested_cell: true`) as requiring an independent confirmatory run with concurrent controls.
2. Document a 5-column Pre-mortem table (`failure_mode`, `early_warning_signal`, `preventive_control`, `stop_loss_trigger`, `fallback_action`).
3. Never coerce missing unit prices, unknown setup fees, or missing machine hours to `0`.

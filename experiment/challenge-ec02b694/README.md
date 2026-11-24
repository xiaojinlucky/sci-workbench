# Bounded Experimental Parameter Optimization (`challenge-ec02b694`)

Offline-native Harbor Skill for bounded parameter optimization over finite device/material domains, interaction-reversal diagnostics, strict null-vs-zero cost arithmetic, and Pareto-ranked 3-version candidate plans.

## Included Components

- `SKILL.md`: 5-stage parameter optimization protocol covering domain conflicts, OFAT interaction reversal, search/stopping selection, cost accounting, and Pareto trade-offs.
- `scripts/feasible_domain_validator.py`: Checks bounds, `safety_max`, step grid, coupled inequalities, linear combination constraints, mutual exclusions, and finite-grid enumeration with infeasibility relaxation advice.
- `scripts/interaction_and_surrogate_analyzer.py`: Pure-stdlib OLS main + 2-way interaction solver detecting OFAT sign reversals, missing factorial cells, extrapolation flags, and prediction uncertainty SE.
- `scripts/cost_resource_calculator.py`: Strict null-vs-zero prospective resource, machine-hour, setup, and labor arithmetic.
- `scripts/candidate_plans_ranker.py`: Exact Pareto frontier computation over `(response, -uncertainty, -cost, -hours)` plus 3-version policy/strategy/stopping validation.
- `scripts/{preflight_probe,budget_guard,result_guard,block_indexer,gap_gate,final_audit}.py`: Deterministic Harbor runtime & grounding gates.
- `references/` & `examples/`: Methodology guide, exact JSON schemas, and illustrative interaction-reversal fixture.

## Dependencies

Python 3.10+ standard library only (zero external dependencies).

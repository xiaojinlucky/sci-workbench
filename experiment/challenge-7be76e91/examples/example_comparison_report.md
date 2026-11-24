# Illustrative Call & Expected Output (`challenge-7be76e91`)

> **Notice**: Illustrative example only — NOT a formal competition case.

- `v1_missing_cost_trap`: Routed to `unresolved_with_reasons` (`one_time_setup_cost`, `unpriced_probe.unit_cost` missing -> NEVER coerced to `$0`, so it cannot steal rank #1 despite `fit=0.92`).
- `v3_over_safety_temp`: Routed to `rejected_with_reasons` (`degas_temp_c=240.0 > safety_max=220.0`).
- `v2_explicit_zero_setup_pareto`: Preserves explicit `one_time_setup_cost: 0.0` in `explicit_zero_fields`, computes exact normalized total cost (`$1881.0` including 10% rework reserve) and DAG schedule (`7.0d`), and is selected with `5/5` weight-profile stability and `$1619.0` budget slack.
